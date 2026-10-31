"""Tick-stream regression: broker history, deduplication, SSE, reconnect and gap visibility."""
from __future__ import annotations

import json
import socket
import threading
import time
from types import SimpleNamespace

import pytest

from forex_ai.dashboard import server
from forex_ai.dashboard.ticks import TickCollector, TickHub, read_tick_history


def test_broker_history_queries_incremental_ticks_not_latest_snapshot():
    class Fake:
        def _remote_eval(self, code):
            assert "mt5.copy_ticks_range(" in code
            assert "mt5.COPY_TICKS_ALL" in code
            assert "symbol_info_tick" not in code
            assert "XAUUSDm" in code
            return {"total": 3, "ticks": [[1,2.3,2.4,6]]}
    r=read_tick_history(Fake(),"XAUUSDm",1000,3000,limit=5)
    assert r["total"] == 3 and len(r["ticks"]) == 1


def test_incremental_boundary_keeps_multiple_ticks_with_same_millisecond():
    hub=TickHub(capacity=40)
    collector=TickCollector(hub)
    ms=1_700_000_000_000
    batch=[[ms,100,101,3],[ms,100.5,101.5,6],[ms+1,101,102,1],[ms+1,101,102,1]]
    assert collector.ingest("XAUUSDm",batch,received_ms=ms+20)==4
    assert collector.ingest("XAUUSDm",batch,received_ms=ms+40)==0
    assert collector.ingest("XAUUSDm",batch+[[ms+1,101,102,1],[ms+2,102,103,3]],
                            received_ms=ms+50)==2
    events,_=hub.after(0)
    assert len([event for event in events if event["type"]=="tick"])==6
    assert hub.quotes["XAUUSDm"]["bid"]==102


def test_capacity_reports_gap_and_event_order_for_slow_clients():
    hub=TickHub(capacity=3)
    for i in range(5):
        hub.publish("tick",{"symbol":"X","bid":i,"received_ms":1000+i})
    result,gap=hub.after(0)
    assert gap and [e["id"] for e in result]==[3,4,5]
    result,gap=hub.after(3)
    assert not gap and [e["id"] for e in result]==[4,5]


def test_history_overflow_emits_visible_gap_before_tail():
    hub=TickHub(capacity=20)
    collector=TickCollector(hub)
    rows=[[1_700_000_000_000,100,101,1]]
    collector.ingest("X",rows,received_ms=1_700_000_000_002,total=9999)
    events,_=hub.after(0)
    assert [e["type"] for e in events]==["gap","tick"]
    assert hub.snapshot()["overflows"]==1


def test_health_transitions_and_collector_runs_without_broker_write(monkeypatch):
    hub=TickHub()
    writes=[]
    class Fake:
        def __init__(self,cfg): pass
        def connect(self):return True
        def close(self):pass
        def order_send(self,*a):writes.append("send");raise AssertionError("order_send called")
    monkeypatch.setattr("forex_ai.dashboard.ticks._resolve_symbols",lambda *a:("XAUUSDm",))
    def fake_read(client,symbol,start,end,limit=4000):
        writes.append("read")
        return {"total":1,"ticks":[[int(time.time()*1000),100,101,1]]}
    monkeypatch.setattr("forex_ai.dashboard.ticks.read_tick_history",fake_read)
    c=TickCollector(hub,config=SimpleNamespace(symbols=("XAUUSD",)),client_factory=Fake,poll_seconds=.2)
    c.start()
    for _ in range(30):
        if hub.snapshot()["quotes"]:break
        time.sleep(.03)
    c.stop()
    assert hub.snapshot()["quotes"]
    assert "send" not in writes
    assert "read" in writes


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix domain sockets required")
def test_stream_sends_valid_sse_headers_snapshot_and_live_ticks(tmp_path,monkeypatch):
    hub=TickHub()
    monkeypatch.setattr(server,"TICK_HUB",hub)
    sock=tmp_path/"server.sock"
    srv=server.ThreadingUnixServer(str(sock),server.Handler)
    bg=threading.Thread(target=srv.serve_forever,daemon=True)
    bg.start()
    client=socket.socket(socket.AF_UNIX)
    client.settimeout(3)
    try:
        client.connect(str(sock))
        client.sendall(b"GET /api/stream/ticks HTTP/1.1\r\nHost: 127.0.0.1:8765\r\nConnection: keep-alive\r\n\r\n")
        data=client.recv(8192)
        assert b"HTTP/1.1 200 OK" in data
        assert b"text/event-stream" in data
        data+=client.recv(8192) if b"event: snapshot" not in data else b""
        assert b"event: snapshot\ndata: " in data
        hub.publish("tick",{"symbol":"XAUUSDm","bid":100.0,"ask":100.4,"received_ms":123456})
        incoming=b""
        for _ in range(5):
            incoming+=client.recv(8192)
            if b"event: tick\ndata: " in incoming:break
        assert b"id: 1\nevent: tick\ndata: " in incoming
        assert b'"symbol":"XAUUSDm"' in incoming
    finally:
        client.close()
        srv.shutdown()
        srv.server_close()
        bg.join(3)


@pytest.mark.skipif(not hasattr(socket, "AF_UNIX"), reason="Unix domain sockets required")
def test_reconnect_after_collector_restart_resets_stale_cursor(tmp_path,monkeypatch):
    hub=TickHub()
    monkeypatch.setattr(server,"TICK_HUB",hub)
    sock=tmp_path/"restart.sock"
    srv=server.ThreadingUnixServer(str(sock),server.Handler)
    bg=threading.Thread(target=srv.serve_forever,daemon=True)
    bg.start()
    client=socket.socket(socket.AF_UNIX)
    client.settimeout(3)
    try:
        client.connect(str(sock))
        client.sendall(
            b"GET /api/stream/ticks HTTP/1.1\r\n"
            b"Host: 127.0.0.1:8765\r\n"
            b"Last-Event-ID: 90000\r\nConnection: keep-alive\r\n\r\n"
        )
        received=b""
        for _ in range(5):
            received+=client.recv(16384)
            # SSE frames can arrive in separate TCP/unix socket reads.
            if b"STREAM_RESTART" in received and b"event: snapshot\ndata: " in received:break
        assert b"event: gap\ndata: " in received
        assert b"STREAM_RESTART" in received
        assert b"event: snapshot\ndata: " in received
    finally:
        client.close()
        srv.shutdown()
        srv.server_close()
        bg.join(3)


def test_status_does_not_require_stream_connection():
    hub=TickHub()
    hub.set_health("RECONNECTING","BROKER_TICK_STREAM_UNAVAILABLE")
    assert hub.snapshot()["stream_state"]=="RECONNECTING"
    assert hub.snapshot()["reason"]=="BROKER_TICK_STREAM_UNAVAILABLE"

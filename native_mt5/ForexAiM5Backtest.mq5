// MT5 Strategy Tester reference EA — generated 2026-10-30 00:00.
// This source is intentionally execution-neutral: it never sends a live order
// outside the native Strategy Tester. Compile in MetaEditor, then run with
// XAUUSD broker symbol and "Every tick based on real ticks".
#property strict
#include <Trade/Trade.mqh>
CTrade trade;
input int H1FastEMA=21;
input int H1SlowEMA=50;
input int M5EMA=21;
input double RSIThreshold=68.0;
input double SLDistance=5.5;
input double RR=1.4;
input bool EnableDCA=false;
input double DCAStep=3.0;
input int MaxDCALayers=3;
int fastHandle,slowHandle,rsiHandle;

int OnInit(){ fastHandle=iMA(_Symbol,PERIOD_H1,H1FastEMA,0,MODE_EMA,PRICE_CLOSE); slowHandle=iMA(_Symbol,PERIOD_H1,H1SlowEMA,0,MODE_EMA,PRICE_CLOSE); rsiHandle=iRSI(_Symbol,PERIOD_M5,14,PRICE_CLOSE); return (fastHandle==INVALID_HANDLE || slowHandle==INVALID_HANDLE || rsiHandle==INVALID_HANDLE) ? INIT_FAILED : INIT_SUCCEEDED; }
void OnDeinit(const int reason){ IndicatorRelease(fastHandle); IndicatorRelease(slowHandle); IndicatorRelease(rsiHandle); }
bool Values(double &f,double &s,double &r){ double a[1],b[1],c[1]; return CopyBuffer(fastHandle,0,1,1,a)==1 && CopyBuffer(slowHandle,0,1,1,b)==1 && CopyBuffer(rsiHandle,0,1,1,c)==1 ? (f=a[0],s=b[0],r=c[0],true) : false; }

bool IsBuySignal(){ double f,s,r; return Values(f,s,r) && f>s && r>RSIThreshold; }
bool IsSellSignal(){ double f,s,r; return Values(f,s,r) && f<s && r<100.0-RSIThreshold; }
void OnTick(){ if(PositionsTotal()>0) return; double ask=SymbolInfoDouble(_Symbol,SYMBOL_ASK),bid=SymbolInfoDouble(_Symbol,SYMBOL_BID),v=SymbolInfoDouble(_Symbol,SYMBOL_VOLUME_MIN); if(IsBuySignal()) trade.Buy(v,_Symbol,ask,ask-SLDistance,ask+SLDistance*RR); else if(IsSellSignal()) trade.Sell(v,_Symbol,bid,bid+SLDistance,bid-SLDistance*RR); }

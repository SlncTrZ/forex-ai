import { useEffect, useRef } from "react";
import Graph from "graphology";
import Sigma from "sigma";
import type { GraphResponse, GraphNode } from "./types";

const COLORS:Record<string,string>={strategy:"#8bffcc",symbol:"#76d5fa",candidate:"#4d9d92",risk:"#edb95f",intent:"#ff6879",execution:"#cfa4ff"};
const rot = (s:string) => {let h=2166136261;for(const c of s)h=Math.imul(h^c.charCodeAt(0),16777619);return (h>>>0)/4294967296};
const TAU=Math.PI*2;
function layout(data:GraphResponse){
  const graph=new Graph({multi:false,type:"directed"});
  const strategies=data.nodes.filter(n=>n.type==="strategy");
  const symbolNodes=data.nodes.filter(n=>n.type==="symbol");
  const centers=new Map<string,[number,number]>();
  strategies.forEach((s,i)=>{const theta=(i/Math.max(1,strategies.length))*TAU-Math.PI/2;centers.set(s.id,[Math.cos(theta)*7,Math.sin(theta)*7]);});
  const parent=new Map(data.edges.filter(e=>e.relation==="GENERATED").map(e=>[e.target,e.source]));
  const candidateStrategy=new Map<string,string>();
  data.nodes.filter(n=>n.type==="candidate").forEach(n=>candidateStrategy.set(n.id,parent.get(n.id)||""));
  const relatedCandidate=new Map<string,string>();
  data.edges.filter(e=>e.relation==="EVALUATED").forEach(e=>relatedCandidate.set(e.target,e.source));
  data.edges.filter(e=>e.relation==="CREATED_INTENT").forEach(e=>relatedCandidate.set(e.target,relatedCandidate.get(e.source)||e.source));
  data.edges.filter(e=>e.relation==="TRANSITIONED").forEach(e=>relatedCandidate.set(e.target,relatedCandidate.get(e.source)||e.source));
  data.nodes.forEach((n,i)=>{
    let x=0,y=0;
    const seed=rot(n.id),spin=TAU*rot(n.id.slice(-14)),radius=0.3+seed*2.1;
    if(n.type==="strategy") [x,y]=centers.get(n.id)||[0,0];
    else if(n.type==="symbol"){const theta=(i/Math.max(1,symbolNodes.length))*TAU;x=14*Math.cos(theta);y=14*Math.sin(theta);}
    else {
      const root=candidateStrategy.get(n.type==="candidate"?n.id:(relatedCandidate.get(n.id)||""));
      const [cx,cy]=centers.get(root||"")||[0,0];
      const multiplier=n.type==="candidate"?2.7:n.type==="risk"?3.8:n.type==="intent"?4.8:5.4;
      const shift=n.type==="candidate"?0:n.type==="risk"?.12:n.type==="intent"?.25:.4;
      x=cx+(radius*multiplier/3)*Math.cos(spin+shift);
      y=cy+(radius*multiplier/3)*Math.sin(spin+shift);
    }
    const isCore=n.type==="strategy";
    graph.addNode(n.id,{x,y,size:isCore?12:n.type==="symbol"?8:n.type==="candidate"?3:n.type==="risk"?3.8:4.2,
      color:COLORS[n.type]||"#84a89b",label:isCore||n.type==="symbol"?String(n.label):undefined,
      zIndex:isCore?2:1,kind:n.type});
  });
  data.edges.forEach(e=>{if(graph.hasNode(e.source)&&graph.hasNode(e.target)&&!graph.hasEdge(e.id)){graph.addEdgeWithKey(e.id,e.source,e.target,{color:"#367f7554",size:.4})}});
  return graph;
}
export function GraphCanvas({data,selected,onSelect}:{data:GraphResponse|null;selected:string|null;onSelect:(node:GraphNode|null)=>void}){
  const container=useRef<HTMLDivElement>(null),renderer=useRef<Sigma|null>(null);
  useEffect(()=>{
    if(!data||!container.current)return;
    const graph=layout(data);
    let sigma:Sigma;
    try{
      sigma=new Sigma(graph,container.current,{renderEdgeLabels:false,labelFont:"JetBrains Mono,monospace",labelColor:{color:"#a7f9e2"},
      labelSize:10,labelWeight:"600",stagePadding:40,labelDensity:.085,labelRenderedSizeThreshold:7,
      defaultEdgeType:"line",allowInvalidContainer:false,zIndex:true,minCameraRatio:.35,maxCameraRatio:4});
    }catch{
      if(container.current)container.current.textContent="WEBGL NOT AVAILABLE — GRAPH API REMAINS READ-ONLY";
      return;
    }
    renderer.current=sigma;
    const onClick=({node}:{node:string})=>onSelect(data.nodes.find(n=>n.id===node)||null);
    const onStage=()=>onSelect(null);
    sigma.on("clickNode",onClick);
    sigma.on("clickStage",onStage);
    return()=>{sigma.kill();renderer.current=null;};
  },[data,onSelect]);
  useEffect(()=>{
    const sigma=renderer.current;if(!sigma)return;
    const g=sigma.getGraph();
    const neighbors=selected&&g.hasNode(selected)?new Set([...g.neighbors(selected),selected]):null;
    sigma.setSetting("nodeReducer",(id,attrs)=>neighbors&&!neighbors.has(id)?{...attrs,color:"#1b3937",label:"",size:Math.max(1,Number(attrs.size)*.6)}:attrs);
    sigma.setSetting("edgeReducer",(id,attrs)=>{
      if(!neighbors||!selected)return attrs;
      const edge=g.extremities(id);
      const active=edge.includes(selected);
      return {...attrs,color:active?"#83f9ca":"#152b2e",size:active?1.5:.25};
    });
    sigma.refresh();
  },[selected,data]);
  return <div className="graph-stage">
    <div className="graph-crosshair"/><div className="graph-orbit graph-orbit-a"/><div className="graph-orbit graph-orbit-b"/>
    <div ref={container} className="sigma-graph" role="region" aria-label="Interactive strategy execution knowledge graph"/>
    {!data&&<div className="graph-placeholder">AWAITING VERIFIED JOURNAL GRAPH…</div>}
    <span className="graph-corner tl">┌</span><span className="graph-corner tr">┐</span><span className="graph-corner bl">└</span><span className="graph-corner br">┘</span>
  </div>;
}

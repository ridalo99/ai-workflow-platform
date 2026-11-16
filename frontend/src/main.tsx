import {useEffect,useState} from "react";
import type {FormEvent} from "react";
import {createRoot} from "react-dom/client";
import "./style.css";
type Doc={id:number;name:string};
type Workflow={id:number;name:string;fields:string[]};
type Run={id:number;document_id:number;workflow_id:number;result:Record<string,string[]|number>};
const labels:Record<string,string>={emails:"E-Mail-Adressen",dates:"Datumsangaben",amounts:"EUR-Beträge",words:"Wortanzahl"};
async function api<T>(path:string,options?:RequestInit):Promise<T>{
 const response=await fetch("/api/"+path,options);
 const body=await response.json();
 if(!response.ok)throw new Error(typeof body.detail==="string"?body.detail:"Eingabe prüfen.");
 return body;
}
const post=(body:unknown)=>({method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
function App(){
 const [docs,D]=useState<Doc[]>([]),[flows,F]=useState<Workflow[]>([]),[runs,R]=useState<Run[]>([]);
 const [tab,T]=useState("Übersicht"),[error,E]=useState(""),[busy,B]=useState(false),[ready,Q]=useState(false);
 const [preview,P]=useState<(Doc&{text:string})|null>(null);
 async function refresh(){const [d,f,r]=await Promise.all([api<Doc[]>("documents"),api<Workflow[]>("workflows"),api<Run[]>("runs")]);D(d);F(f);R(r);Q(true)}
 useEffect(()=>{refresh().catch(e=>E(String(e.message)))},[]);
 async function act(e:FormEvent<HTMLFormElement>,action:(f:FormData)=>Promise<unknown>,next?:string){e.preventDefault();const form=e.currentTarget;const data=new FormData(form);E("");B(true);try{await action(data);await refresh();form.reset();if(next)T(next)}catch(err){E(err instanceof Error?err.message:String(err))}finally{B(false)}}
 return <><aside><strong><i>◆</i> Workflow Studio</strong><small>ARBEITSBEREICH</small>{["Übersicht","Dokumente","Workflows","Ergebnisse"].map((name)=><button key={name} className={tab===name?"active":""} aria-current={tab===name?"page":undefined} onClick={()=>T(name)}>{name}</button>)}<footer>● Lokal · regelbasierte Extraktion</footer></aside>
 <main><header><span>WORKFLOW STUDIO / {tab.toUpperCase()}</span><h1>{tab}</h1><p>Dokumente organisieren, Informationen extrahieren und Abläufe wiederverwenden.</p></header>
 {error&&<p role="alert" className="error">{error}</p>}
 {!ready&&!error&&<p>Arbeitsbereich wird geladen …</p>}
 {tab==="Übersicht"&&<><div className="stats">{[[docs.length,"Dokumente"],[flows.length,"Workflows"],[runs.length,"Ausführungen"]].map(([n,label])=><article key={label}><small>{label}</small><b>{n}</b></article>)}</div><article><h2>Dein erster Ablauf</h2><p>Lade ein Dokument hoch, lege fest, welche Informationen du brauchst, und starte den Workflow.</p><button className="primary" onClick={()=>T("Dokumente")}>Dokument hochladen</button></article></>}
 {tab==="Dokumente"&&<><article><h2>Dokument hinzufügen</h2><form onSubmit={e=>act(e,f=>api("documents",{method:"POST",body:f}))}><label>PDF, TXT oder Markdown · bis 2 MB<input type="file" name="file" accept=".pdf,.txt,.md" required /></label><button disabled={busy} className="primary">Speichern</button></form></article><article><h2>Dokumente</h2>{docs.length===0&&<p>Noch keine Dokumente.</p>}{docs.map(d=><button className="row" key={d.id} onClick={()=>api<Doc&{text:string}>("documents/"+d.id).then(P).catch(e=>E(e.message))}>{d.name} <span>Text ansehen →</span></button>)}{preview&&<><h3>{preview.name}</h3><pre>{preview.text}</pre></>}</article></>}
 {tab==="Workflows"&&<><article><h2>Workflow erstellen</h2><form onSubmit={e=>act(e,f=>api("workflows",post({name:f.get("name"),fields:f.getAll("fields")})))}><label>Name<input name="name" placeholder="Rechnungsdaten erfassen" required maxLength={80}/></label><fieldset><legend>Informationen extrahieren</legend>{Object.entries(labels).map(([key,label])=><label key={key} className="check"><input type="checkbox" name="fields" value={key}/>{label}</label>)}</fieldset><button disabled={busy} className="primary">Workflow speichern</button></form></article><article><h2>Workflow ausführen</h2><form onSubmit={e=>act(e,f=>api("runs",post({document_id:Number(f.get("document")),workflow_id:Number(f.get("workflow"))})),"Ergebnisse")}><label>Dokument<select name="document" required><option value="">Auswählen</option>{docs.map(d=><option key={d.id} value={d.id}>{d.name}</option>)}</select></label><label>Workflow<select name="workflow" required><option value="">Auswählen</option>{flows.map(f=><option key={f.id} value={f.id}>{f.name}</option>)}</select></label><button disabled={busy||!docs.length||!flows.length} className="primary">Ausführen</button></form></article></>}
 {tab==="Ergebnisse"&&<>{!runs.length&&<article>Noch keine Ausführungen.</article>}{runs.map(r=><article key={r.id}><small>AUSFÜHRUNG #{r.id}</small><h2>{flows.find(f=>f.id===r.workflow_id)?.name}</h2><p>{docs.find(d=>d.id===r.document_id)?.name}</p>{Object.entries(r.result).map(([key,v])=><div className="result" key={key}><strong>{labels[key]}</strong><span>{Array.isArray(v)?v.join(", ")||"Keine Treffer":v}</span></div>)}</article>)}</>}
 </main></>;
}
createRoot(document.getElementById("root")!).render(<App/>);

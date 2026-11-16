import {useEffect,useState} from "react";
import type {FormEvent} from "react";
import {createRoot} from "react-dom/client";
import "./style.css";
type Doc={id:number;name:string};
type Workflow={id:number;name:string;fields:string[]};
type Evidence={value:string|null;quote:string|null;status:"quoted"|"missing"|"unverified"};
type Job={id:number;document_id:number;workflow_id:number;status:string;error:string|null;run_id:number|null};
const jobStatus:Record<string,string>={queued:"Wartet",running:"Läuft",completed:"Fertig",failed:"Fehlgeschlagen"};
type Run={id:number;document_id:number;workflow_id:number;result:Record<string,string[]|number|string|Evidence>};
const labels:Record<string,string>={emails:"E-Mail-Adressen",dates:"Datumsangaben",amounts:"EUR-Beträge",words:"Wortanzahl",summary:"KI-Zusammenfassung (Ollama)",approved_amount:"Freigegebener Betrag (KI)"};
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
 const [jobs,J]=useState<Job[]>([]);
 const [edit,Edit]=useState<Workflow|null>(null);
 const [preview,P]=useState<(Doc&{text:string})|null>(null);
 async function refresh(){const [d,f,r,j]= await Promise.all([api<Doc[]>("documents"),api<Workflow[]>("workflows"),api<Run[]>("runs"),api<Job[]>("jobs")]);J(j);D(d);F(f);R(r);Q(true)}
 useEffect(()=>{refresh().catch(e=>E(String(e.message)))},[]);
 useEffect(()=>{
  if(!jobs.some(j=>j.status==="queued"||j.status==="running"))return;
  const timer=window.setTimeout(()=>refresh().catch(e=>{E(e.message);J(current=>[...current])}),1500);
  return ()=>window.clearTimeout(timer);
 },[jobs]);
 async function act(e:FormEvent<HTMLFormElement>,action:(f:FormData)=>Promise<unknown>,next?:string){e.preventDefault();const form=e.currentTarget;const data=new FormData(form);E("");B(true);try{await action(data);await refresh();form.reset();if(next)T(next)}catch(err){E(err instanceof Error?err.message:String(err))}finally{B(false)}}
 return <><aside><strong><i>◆</i> Workflow Studio</strong><small>ARBEITSBEREICH</small>{["Übersicht","Dokumente","Workflows","Ergebnisse"].map((name)=><button key={name} className={tab===name?"active":""} aria-current={tab===name?"page":undefined} onClick={()=>T(name)}>{name}</button>)}<footer>● Lokal · Regeln und optional KI</footer></aside>
 <main><header><span>WORKFLOW STUDIO / {tab.toUpperCase()}</span><h1>{tab}</h1><p>Dokumente organisieren, Informationen extrahieren und Abläufe wiederverwenden.</p></header>
 {error&&<p role="alert" className="error">{error}</p>}
 {!ready&&!error&&<p>Arbeitsbereich wird geladen …</p>}
 {tab==="Übersicht"&&<><div className="stats">{[[docs.length,"Dokumente"],[flows.length,"Workflows"],[runs.length,"Ausführungen"]].map(([n,label])=><article key={label}><small>{label}</small><b>{n}</b></article>)}</div><article><h2>Dein erster Ablauf</h2><p>Lade ein Dokument hoch, lege fest, welche Informationen du brauchst, und starte den Workflow.</p><button className="primary" onClick={()=>T("Dokumente")}>Dokument hochladen</button></article></>}
 {tab==="Dokumente"&&<><article><h2>Dokument hinzufügen</h2><form onSubmit={e=>act(e,f=>api("documents",{method:"POST",body:f}))}><label>PDF, TXT oder Markdown · bis 2 MB<input type="file" name="file" accept=".pdf,.txt,.md" required /></label><button disabled={busy} className="primary">Speichern</button></form></article><article><h2>Dokumente</h2>{docs.length===0&&<p>Noch keine Dokumente.</p>}{docs.map(d=><button className="row" key={d.id} onClick={()=>api<Doc&{text:string}>("documents/"+d.id).then(P).catch(e=>E(e.message))}>{d.name} <span>Text ansehen →</span></button>)}{preview&&<><h3>{preview.name}</h3><pre>{preview.text}</pre><form onSubmit={e=>{if(!confirm("Dokument und seine Ergebnisse löschen?")){e.preventDefault();return}void act(e,async()=>{const response=await fetch("/api/documents/"+preview.id,{method:"DELETE"});if(!response.ok)throw new Error("Löschen fehlgeschlagen.");P(null)})}}><button disabled={busy}>Dokument löschen</button></form></>}</article></>}
 {tab==="Workflows"&&<><article><h2>{edit?"Workflow bearbeiten":"Workflow erstellen"}</h2><form key={edit?.id??"new"} onSubmit={e=>act(e,async f=>{await api(edit?"workflows/"+edit.id:"workflows",{...post({name:f.get("name"),fields:f.getAll("fields")}),method:edit?"PUT":"POST"});Edit(null)})}><label>Name<input name="name" defaultValue={edit?.name} placeholder="Rechnungsdaten erfassen" required maxLength={80}/></label><fieldset><legend>Informationen extrahieren</legend>{Object.entries(labels).map(([key,label])=><label key={key} className="check"><input type="checkbox" name="fields" value={key} defaultChecked={edit?.fields.includes(key)}/>{label}</label>)}</fieldset><button disabled={busy} className="primary">Workflow speichern</button>{edit&&<button type="button" onClick={()=>Edit(null)}>Abbrechen</button>}</form></article><article><h2>Gespeicherte Workflows</h2>{flows.map(f=><button key={f.id} className="row" onClick={()=>Edit(f)}>{f.name}<span>Bearbeiten</span></button>)}</article><article><h2>Workflow ausführen</h2><form onSubmit={e=>act(e,f=>api("jobs",post({document_id:Number(f.get("document")),workflow_id:Number(f.get("workflow"))})),"Ergebnisse")}><label>Dokument<select name="document" required><option value="">Auswählen</option>{docs.map(d=><option key={d.id} value={d.id}>{d.name}</option>)}</select></label><label>Workflow<select name="workflow" required><option value="">Auswählen</option>{flows.map(f=><option key={f.id} value={f.id}>{f.name}</option>)}</select></label><button disabled={busy||!docs.length||!flows.length} className="primary">Ausführen</button></form></article></>}
 {tab==="Ergebnisse"&&<>{jobs.length>0&&<article><h2>Aufträge</h2>{jobs.map(j=><div className="result" key={j.id}><strong>#{j.id} · {flows.find(f=>f.id===j.workflow_id)?.name}</strong><span>{jobStatus[j.status]}{j.run_id&&<> · <a href={`#run-${j.run_id}`}>Ergebnis #{j.run_id}</a></>}{j.error&&<><br/>{j.error}</>}</span></div>)}</article>}{!runs.length&&<article>Noch keine Ausführungen.</article>}{runs.map(r=><article key={r.id} id={`run-${r.id}`}><small>AUSFÜHRUNG #{r.id}</small><h2>{flows.find(f=>f.id===r.workflow_id)?.name}</h2><p>{docs.find(d=>d.id===r.document_id)?.name}</p>{Object.entries(r.result).filter(([key])=>key!=="amount_review").map(([key,v])=><div className="result" key={key}><strong>{labels[key]}</strong><span>{Array.isArray(v)?v.join(", ")||"Keine Treffer":typeof v==="object"?<>{v.status==="quoted"?<>{v.value}<br/><q>{v.quote}</q><br/><small>Zitat gefunden · Bedeutung prüfen</small></>:v.status==="missing"?"Kein eindeutiger Betrag laut Modell":"Kein überprüfbarer Beleg"}</>:v}</span></div>)}{r.result.approved_amount&&<form className="amount-review" key={String(r.result.amount_review??"")} onSubmit={e=>act(e,f=>api("runs/"+r.id+"/amount-review",{...post({value:f.get("value")}),method:"PUT"}))}>
 <label>Geprüfter Betrag (z. B. 3.200,00 EUR)<input name="value" required maxLength={25} pattern="(?:[0-9]{1,12}|[0-9]{1,3}(?:\.[0-9]{3}){1,3}),[0-9]{2} EUR" defaultValue={typeof r.result.amount_review==="string"?r.result.amount_review:(r.result.approved_amount as Evidence).value??""}/></label>
 <button className="primary" disabled={busy}>Prüfwert speichern</button>
 {typeof r.result.amount_review==="string"&&<p>Gespeichert: <strong>{r.result.amount_review}</strong> · <a href={`/api/runs/${r.id}/amount-export/csv`}>CSV</a> · <a href={`/api/runs/${r.id}/amount-export/json`}>JSON</a></p>}
 </form>}</article>)}</>}
 </main></>;
}
createRoot(document.getElementById("root")!).render(<App/>);

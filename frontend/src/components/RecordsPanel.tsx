import { useEffect, useState } from "react";
import { api } from "../api";

export function RecordsPanel({ kind }: { kind: "coupons" | "publications" }) {
  const [records, setRecords] = useState<Array<Record<string, unknown>>>([]);
  const [error, setError] = useState("");
  useEffect(() => { const load = kind === "coupons" ? api.coupons : api.publications; load().then(setRecords).catch((reason: Error) => setError(reason.message)); }, [kind]);
  const title = kind === "coupons" ? "Cupons" : "Publicações";
  return <section aria-labelledby="records-title"><p className="label">Histórico operacional</p><h2 id="records-title" className="mt-2 text-4xl font-black">{title}</h2>{error ? <p role="alert" className="mt-4 text-danger">{error}</p> : null}<div className="mt-8 space-y-3">{records.map((record) => <article key={String(record.id)} className="card"><p className="font-bold">{String(record.title ?? record.mode ?? record.id)}</p><pre className="mt-3 overflow-x-auto text-xs text-slate-500">{JSON.stringify(record, null, 2)}</pre></article>)}{records.length === 0 ? <div className="card text-center text-slate-500">Nenhum registro ainda.</div> : null}</div></section>;
}

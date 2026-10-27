import { ExternalLink, Send } from "lucide-react";
import { useEffect, useState } from "react";
import { api } from "../api";
import type { PublicationRecord } from "../types";
import { StatusBadge } from "./StatusBadge";

const date = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" });
export function PublicationHistory() {
  const [records, setRecords] = useState<PublicationRecord[]>([]);
  useEffect(() => { void api.publications().then(setRecords); }, []);
  return <section aria-labelledby="publication-title"><p className="label">Rastreabilidade</p><h2 id="publication-title" className="page-title">Publicações</h2><div className="mt-8 space-y-3">{records.map((item) => { const username = item.destination.startsWith("@") ? item.destination.slice(1) : null; const href = item.public_message_url ?? (username && item.telegram_message_id ? `https://t.me/${username}/${item.telegram_message_id}` : null); return <article key={item.id} className={`card flex flex-wrap items-center gap-4 ${item.historical_duplicate ? "border-amber-300" : ""}`}><span className={`rounded-2xl p-3 ${item.publication_kind === "BAD_OFFER" ? "bg-rose-50 text-rose-700" : "bg-violet-50 text-violet-700"}`}><Send /></span><div className="min-w-[220px] flex-1"><h3 className="font-bold">{item.product_title}</h3><p className="mt-1 text-sm text-slate-500">{item.published_at ? date.format(new Date(item.published_at)) : "Pendente"} • mensagem {item.telegram_message_id ?? "—"}</p><div className="mt-2 flex flex-wrap gap-2"><span className="badge bg-slate-100 text-slate-700">{item.publication_kind.replaceAll("_", " ")}</span>{item.historical_duplicate ? <span className="badge bg-amber-50 text-amber-700">Duplicidade histórica</span> : null}</div></div><StatusBadge value={item.delivery_status || item.mode} />{href ? <a className="button-secondary" href={href} target="_blank" rel="noreferrer"><ExternalLink size={15} />Abrir</a> : null}</article>; })}{records.length === 0 ? <div className="card text-center text-slate-500">Nenhuma publicação registrada.</div> : null}</div></section>;
}

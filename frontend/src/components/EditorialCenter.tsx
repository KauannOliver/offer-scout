import { AlertTriangle, ExternalLink, FileCheck2, Play, ShieldCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { BadOfferRecord, DuplicateReport, EditorialStatus } from "../types";
import { StatusBadge } from "./StatusBadge";

type Props = { onAction: (run: () => Promise<unknown>, message: string) => Promise<void> };

export function EditorialCenter({ onAction }: Props) {
  const [duplicates, setDuplicates] = useState<DuplicateReport | null>(null);
  const [analyses, setAnalyses] = useState<BadOfferRecord[]>([]);
  const [status, setStatus] = useState<EditorialStatus | null>(null);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);

  const load = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [nextDuplicates, nextAnalyses, nextStatus] = await Promise.all([api.duplicateReport(), api.badOffers(), api.badOfferStatus()]);
      setDuplicates(nextDuplicates); setAnalyses(nextAnalyses); setStatus(nextStatus);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Falha ao carregar a central editorial");
    } finally { setLoading(false); }
  }, []);

  useEffect(() => {
    let active = true;
    Promise.all([api.duplicateReport(), api.badOffers(), api.badOfferStatus()])
      .then(([nextDuplicates, nextAnalyses, nextStatus]) => {
        if (active) { setDuplicates(nextDuplicates); setAnalyses(nextAnalyses); setStatus(nextStatus); }
      })
      .catch((reason: unknown) => {
        if (active) setError(reason instanceof Error ? reason.message : "Falha ao carregar a central editorial");
      })
      .finally(() => { if (active) setLoading(false); });
    return () => { active = false; };
  }, []);

  async function run(action: () => Promise<unknown>, message: string) {
    await onAction(action, message); await load();
  }

  return <section aria-labelledby="editorial-title">
    <div className="flex flex-wrap items-end justify-between gap-4"><div><p className="label">Confiança e governança</p><h2 id="editorial-title" className="page-title">Central editorial</h2><p className="mt-2 max-w-3xl text-slate-500">Controle de duplicidade, análises financeiras e manifesto público em um só lugar.</p></div><div className="flex flex-wrap gap-2"><button className="button-secondary" onClick={() => void run(api.publishManifest, "Manifesto enviado para publicação e fixação.")}><FileCheck2 size={16}/>Publicar manifesto</button><button className="button-primary" onClick={() => void run(api.runBadOffer, "Análise editorial enviada; ela só será publicada com evidência suficiente.")}><Play size={16}/>Executar análise</button></div></div>
    {error ? <p role="alert" className="mt-5 rounded-2xl bg-red-50 p-4 text-red-700">{error}</p> : null}
    <div className="mt-7 grid gap-4 sm:grid-cols-2 xl:grid-cols-4">
      <Metric icon={ShieldCheck} label="Duplicidades ativas" value={duplicates?.active_duplicate_groups ?? 0} tone="emerald" />
      <Metric icon={AlertTriangle} label="Produtos históricos repetidos" value={duplicates?.historical_products ?? 0} tone="amber" />
      <Metric icon={FileCheck2} label="Filas neutralizadas" value={duplicates?.neutralized_queue_items ?? 0} tone="violet" />
      <Metric icon={Play} label="Análises editoriais" value={analyses.length} tone="blue" />
    </div>
    <div className="card mt-6 border-blue-100 bg-blue-50/60"><div className="flex flex-wrap items-center justify-between gap-4"><div><p className="label text-blue-700">Prontidão editorial</p><p className="mt-2 text-sm text-slate-700">O radar acompanha candidatos até reunir histórico, frescor e confiança suficientes. Uma cota vazia é preferível a uma recomendação sem evidência.</p></div><div className="flex flex-wrap gap-2">{Object.entries(status?.counts ?? {}).map(([state, count]) => <span key={state} className="badge bg-white text-slate-700">{state.replaceAll("_", " ")}: {count}</span>)}</div></div><p className="mt-3 text-xs text-slate-500">Próxima tentativa real: {status?.next_attempt_at ? new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" }).format(new Date(status.next_attempt_at)) : "aguardando o próximo slot configurado"}</p></div>
    <div className="mt-7 grid gap-6 xl:grid-cols-[1.2fr_.8fr]">
      <div><h3 className="text-xl font-black">Análises de ofertas ruins</h3><div className="mt-4 space-y-3">{analyses.map((item) => <article key={item.id} className="card flex gap-4">{item.image_url ? <img src={item.image_url} alt="" className="h-20 w-20 rounded-2xl bg-slate-50 object-contain"/> : <div className="grid h-20 w-20 place-items-center rounded-2xl bg-slate-100 text-slate-400"><AlertTriangle size={20}/></div>}<div className="min-w-0 flex-1"><div className="flex flex-wrap items-center gap-2"><StatusBadge value={item.status}/><span className="badge bg-rose-50 text-rose-700">BAD OFFER</span></div><h4 className="mt-2 font-bold">{item.product_title}</h4><p className="mt-2 text-sm text-slate-500">{item.payload.evidence?.reasons?.join(" • ") || "Evidências registradas na execução."}</p>{item.public_message_url ? <a className="mt-3 inline-flex items-center gap-1 text-sm font-bold text-blue-700" href={item.public_message_url} target="_blank" rel="noreferrer">Abrir no canal <ExternalLink size={14}/></a> : null}</div></article>)}{!loading && analyses.length === 0 ? <div className="card text-center text-slate-500">Nenhuma análise foi publicada: o sistema não preenche cotas sem evidência.</div> : null}</div></div>
      <div><h3 className="text-xl font-black">Histórico preservado</h3><div className="card mt-4"><p className="text-sm text-slate-500">As mensagens antigas não foram apagadas. O relatório apenas sinaliza o histórico e confirma que novas duplicidades ativas estão bloqueadas.</p><div className="mt-4 max-h-[480px] space-y-3 overflow-y-auto">{duplicates?.historical.map((item) => <div key={item.product_id} className="rounded-2xl bg-slate-50 p-3"><p className="font-bold">{item.title}</p><p className="mt-1 text-xs text-amber-700">{item.count} publicações históricas</p></div>)}</div></div></div>
    </div>
  </section>;
}

function Metric({ icon: Icon, label, value, tone }: { icon: typeof ShieldCheck; label: string; value: number; tone: "emerald" | "amber" | "violet" | "blue" }) {
  const tones = { emerald: "bg-emerald-50 text-emerald-700", amber: "bg-amber-50 text-amber-700", violet: "bg-violet-50 text-violet-700", blue: "bg-blue-50 text-blue-700" };
  return <div className="card"><div className={`inline-flex rounded-xl p-2 ${tones[tone]}`}><Icon size={18}/></div><p className="mt-4 text-3xl font-black">{value}</p><p className="mt-1 text-sm text-slate-500">{label}</p></div>;
}

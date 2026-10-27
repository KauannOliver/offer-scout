import { AlertTriangle, BadgePercent, Boxes, CheckCircle2, Clock3, RadioTower, Send, TicketCheck } from "lucide-react";
import type { DashboardStatus, Opportunity } from "../types";
import { StatusBadge } from "./StatusBadge";

const date = new Intl.DateTimeFormat("pt-BR", {
  dateStyle: "short",
  timeStyle: "short",
  timeZone: "America/Sao_Paulo",
});

export function Overview({ status, opportunities }: { status: DashboardStatus; opportunities: Opportunity[] }) {
  const cards = [
    { label: "Monitorados", value: status.product_count, icon: Boxes, tone: "bg-blue-50 text-blue-700" },
    { label: "Elegíveis", value: status.eligible_count, icon: CheckCircle2, tone: "bg-emerald-50 text-emerald-700" },
    { label: "Publicados 24h", value: `${status.published_24h}/${status.daily_limit}`, icon: Send, tone: "bg-violet-50 text-violet-700" },
    { label: "Com cupom", value: status.coupon_coverage, icon: TicketCheck, tone: "bg-amber-50 text-amber-700" },
  ];
  const best = opportunities
    .filter((item) => item.eligible)
    .sort((a, b) => Number(b.score ?? 0) - Number(a.score ?? 0))
    .slice(0, 3);
  return <section aria-labelledby="overview-title" className="space-y-7">
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div><p className="label">Central operacional</p><h2 id="overview-title" className="page-title">Visão geral</h2><p className="mt-2 text-slate-500">Da descoberta à publicação, com cada condição revalidada.</p></div>
      <div className="flex items-center gap-2 rounded-2xl border border-black/5 bg-white px-4 py-3 shadow-sm"><RadioTower size={18} /><StatusBadge value={status.delivery_mode} /></div>
    </div>
    <div className="grid gap-4 sm:grid-cols-2 xl:grid-cols-4">{cards.map(({ label, value, icon: Icon, tone }) => <article key={label} className="card flex items-start justify-between"><div><p className="text-3xl font-black tracking-tight">{value}</p><p className="mt-1 text-sm text-slate-500">{label}</p></div><span className={`rounded-2xl p-3 ${tone}`}><Icon size={20} /></span></article>)}</div>
    <div className="grid gap-5 xl:grid-cols-[1.25fr_.75fr]">
      <article className="card">
        <div className="flex items-center justify-between"><div><p className="label">Radar</p><h3 className="mt-2 text-xl font-bold">Melhores oportunidades</h3></div><BadgePercent className="text-brand-blue" /></div>
        <div className="mt-5 divide-y divide-black/5">{best.map((item) => <div key={item.id} className="flex items-center gap-4 py-4 first:pt-0 last:pb-0"><img src={item.image_url ?? ""} alt="" className="h-14 w-14 rounded-xl object-contain bg-white" /><div className="min-w-0 flex-1"><p className="truncate font-semibold">{item.title}</p><p className="text-sm text-slate-500">{item.discount_percent ? `${Number(item.discount_percent).toFixed(0)}% OFF` : "Sem desconto informado"} • Score {item.score ?? "—"}</p></div><StatusBadge value={item.queue_status ?? item.classification ?? "MONITORADO"} /></div>)}{best.length === 0 ? <p className="py-8 text-center text-slate-500">Aguardando ofertas elegíveis.</p> : null}</div>
      </article>
      <article className="card">
        <p className="label">Saúde da operação</p><h3 className="mt-2 text-xl font-bold">Serviços e agenda</h3>
        <dl className="mt-5 space-y-4 text-sm">
          <Health label="Navegador" value={status.browser_state} ok={status.browser_state === "AUTHENTICATED"} />
          <Health label="Canal público" value={status.public_channel_configured ? "CONFIGURADO" : "PENDENTE"} ok={status.public_channel_configured} />
          <Health label="Groq" value={status.groq_configured ? "CONFIGURADA" : "FALLBACK ATIVO"} ok />
          <Health label="Falhas na fila" value={String(status.failed_count)} ok={status.failed_count === 0} />
        </dl>
        <div className="mt-6 rounded-2xl bg-slate-950 p-4 text-white"><div className="flex gap-3"><Clock3 className="mt-0.5 text-signal" size={18} /><div><p className="text-xs uppercase tracking-widest text-white/50">Próxima janela</p><p className="mt-1 font-semibold">{status.next_publication_at ? date.format(new Date(status.next_publication_at)) : publicationReason(status.next_publication_reason)}</p>{status.next_publication_at ? <p className="mt-1 text-xs text-white/45">Previsão recalculada pela fila e pelos limites atuais</p> : null}</div></div></div>
      </article>
    </div>
    {status.failed_count > 0 ? <div className="flex gap-3 rounded-2xl border border-red-100 bg-red-50 p-4 text-sm text-red-800"><AlertTriangle size={18} /><span>Há {status.failed_count} item(ns) que precisam de revisão na fila.</span></div> : null}
  </section>;
}

function Health({ label, value, ok }: { label: string; value: string; ok: boolean }) {
  return <div className="flex items-center justify-between gap-3"><dt className="text-slate-500">{label}</dt><dd className={`font-bold ${ok ? "text-emerald-700" : "text-red-700"}`}>{value.replaceAll("_", " ")}</dd></div>;
}

function publicationReason(value: string) {
  const labels: Record<string, string> = {
    PUBLICATION_DISARMED: "Publicação automática desarmada",
    PUBLICATION_IN_PROGRESS: "Uma publicação está em andamento",
    WAITING_READY_OFFER: "Aguardando uma oferta pronta",
    DAILY_LIMIT: "Limite diário atingido",
  };
  return labels[value] ?? "Aguardando a próxima condição válida";
}

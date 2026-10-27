import { useEffect, useState } from "react";
import { api } from "../api";
import type { OperationalCalendar } from "../types";
import { useQueryState } from "../navigation";
import { WeeklyReportPanel } from "./WeeklyReportPanel";

const kinds: Record<string, string> = { GOOD_OFFER: "Oferta", BAD_OFFER: "Análise financeira", COUPON: "Cupom", BOT_PROMO: "Atendimento privado", TRUST_REMINDER: "Lembrete do canal", MANIFEST: "Apresentação" };
const states: Record<string, string> = { PENDING: "Agendado", RUNNING: "Em execução", PUBLISHED: "Publicado", SKIPPED: "Omitido", EXPIRED: "Janela encerrada", RECONCILIATION: "Conferência necessária" };
function saoPauloDate(value: string) {
  return new Date(value).toLocaleString("pt-BR", { timeZone: "America/Sao_Paulo", dateStyle: "short", timeStyle: "short" });
}

export function CalendarPanel() {
  const [data, setData] = useState<OperationalCalendar | null>(null);
  const [error, setError] = useState("");
  const [kind, setKind] = useQueryState("calendar.kind", "");
  const [state, setState] = useQueryState("calendar.state", "");
  const [rawPage, setPage] = useQueryState("calendar.page", "1");
  const [loading, setLoading] = useState(true);
  const page = Math.max(1, Number(rawPage) || 1);
  useEffect(() => {
    let active = true;
    let inFlight = false;
    const load = async () => {
      if (inFlight) return;
      inFlight = true;
      setLoading(true);
      try {
        const value = await api.calendar(new URLSearchParams({ page: String(page), kind, state }).toString());
        if (active) { setData(value); setError(""); }
      } catch (reason) { if (active) setError(reason instanceof Error ? reason.message : "Falha ao carregar calendário"); }
      finally { inFlight = false; if (active) setLoading(false); }
    };
    void load(); const timer = window.setInterval(() => void load(), 30000);
    return () => { active = false; window.clearInterval(timer); };
  }, [page, kind, state]);
  return <section className="card space-y-4" aria-busy={loading}>
    <header><h3 className="text-xl font-bold">Calendário operacional</h3><p className="mt-1 text-sm text-slate-500">Horários de São Paulo. Tentativas perdidas podem ser retomadas dentro da janela; evidências e limites continuam obrigatórios.</p></header>
    {error ? <p role="alert" className="notice bg-red-50">{error}</p> : null}
    <div className="grid gap-3 sm:grid-cols-2">{data?.discoveries.map(item => <div key={item.strategy} className="rounded-xl bg-amber-50 p-4"><strong>{item.strategy === "relevant" ? "Feed principal" : "Rotação de categorias"}</strong><p className="mt-2 text-sm">Próxima tentativa: {saoPauloDate(item.next_due_at)}</p><p className="text-xs">A cada {item.interval_minutes} minutos</p></div>)}</div>
    <div className="grid gap-3 sm:grid-cols-2">
      <label className="text-sm font-semibold">Tipo<select aria-label="Tipo de publicação" className="input mt-1" value={kind} onChange={e => { setKind(e.target.value); setPage("1"); }}><option value="">Todos os tipos</option>{Object.entries(kinds).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
      <label className="text-sm font-semibold">Estado<select aria-label="Estado da tentativa" className="input mt-1" value={state} onChange={e => { setState(e.target.value); setPage("1"); }}><option value="">Todos os estados</option>{Object.entries(states).map(([key, label]) => <option key={key} value={key}>{label}</option>)}</select></label>
    </div>
    {!data ? <p role="status">Carregando calendário…</p> : data.slots.length === 0 ? <p className="rounded-xl bg-slate-50 p-5 text-sm">Nenhuma tentativa neste filtro.</p> : <ol className="divide-y">{data.slots.map(slot => <li key={slot.id} className="space-y-2 py-4 text-sm">
      <div className="flex flex-wrap justify-between gap-2"><strong>{kinds[slot.kind] ?? slot.kind}</strong><span className={slot.state === "RECONCILIATION" ? "rounded-full bg-red-100 px-3 py-1 text-xs font-semibold text-red-900" : "rounded-full bg-slate-100 px-3 py-1 text-xs font-semibold"}>{states[slot.state] ?? slot.state}</span></div>
      <p>Previsto: {saoPauloDate(slot.scheduled_for)}</p>
      {slot.next_attempt_at ? <p className="font-medium">Próxima tentativa: {saoPauloDate(slot.next_attempt_at)}</p> : null}
      <p className="break-words text-xs text-slate-500">{slot.reason?.replaceAll("_", " ") ?? "Aguardando avaliação"}</p>
    </li>)}</ol>}
    <footer className="flex flex-wrap items-center justify-between gap-3"><button className="button-secondary" disabled={page <= 1 || loading} onClick={() => setPage(String(page-1))}>Anterior</button><span className="text-sm">Página {page} • {data?.total ?? 0} tentativas</span><button className="button-secondary" disabled={loading || !data || page*data.page_size >= data.total} onClick={() => setPage(String(page+1))}>Próxima</button></footer>
    <WeeklyReportPanel />
  </section>;
}

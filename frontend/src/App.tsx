import { useCallback, useEffect, useState } from "react";
import { BadgePercent, BookOpenCheck, Gauge, ListChecks, LogOut, Menu, Play, RadioTower, RefreshCw, Send, Settings, Tags, Workflow, X } from "lucide-react";
import { api } from "./api";
import { useRoute } from "./navigation";
import { CouponCenter } from "./components/CouponCenter";
import { EditorialCenter } from "./components/EditorialCenter";
import { Login } from "./components/Login";
import { OpportunityGrid } from "./components/OpportunityGrid";
import { Overview } from "./components/Overview";
import { OperationsCenter } from "./components/OperationsCenter";
import { PreviewDialog } from "./components/PreviewDialog";
import { PublicationHistory } from "./components/PublicationHistory";
import { QueueTable } from "./components/QueueTable";
import { SettingsPanel } from "./components/SettingsPanel";
import type { CreativePreview, DashboardStatus, Opportunity } from "./types";

type View = "overview" | "operations" | "opportunities" | "queue" | "coupons" | "editorial" | "publications" | "settings";
const navigation = [
  { id: "overview" as const, label: "Visão geral", icon: Gauge },
  { id: "operations" as const, label: "Operações", icon: Workflow },
  { id: "opportunities" as const, label: "Oportunidades", icon: BadgePercent },
  { id: "queue" as const, label: "Fila operacional", icon: ListChecks },
  { id: "coupons" as const, label: "Cupons", icon: Tags },
  { id: "editorial" as const, label: "Confiança editorial", icon: BookOpenCheck },
  { id: "publications" as const, label: "Publicações", icon: Send },
  { id: "settings" as const, label: "Configurações", icon: Settings },
];

const viewIds = navigation.map((item) => item.id);

export default function App() {
  const [user, setUser] = useState<string | null>(null);
  const [view, setView] = useRoute<View>(viewIds, "overview");
  const [status, setStatus] = useState<DashboardStatus | null>(null);
  const [opportunities, setOpportunities] = useState<Opportunity[]>([]);
  const [preview, setPreview] = useState<CreativePreview | null>(null);
  const [notice, setNotice] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [actionBusy, setActionBusy] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

  const refresh = useCallback(async () => {
    setLoading(true); setError("");
    try {
      const [nextStatus, nextOpportunities] = await Promise.all([api.status(), api.opportunityPage({ page: 1, eligible: true, sort: "score" })]);
      setStatus(nextStatus); setOpportunities(nextOpportunities.items);
    } catch (reason) { setError(reason instanceof Error ? reason.message : "Falha ao atualizar"); }
    finally { setLoading(false); }
  }, []);

  useEffect(() => { api.me().then((result) => { setUser(result.username); return refresh(); }).catch(() => setLoading(false)); }, [refresh]);

  async function action(run: () => Promise<unknown>, success = "Ação enviada com sucesso.") {
    setError(""); setNotice(""); setActionBusy(true);
    try { await run(); setNotice(success); await refresh(); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Falha na ação"); }
    finally { setActionBusy(false); }
  }
  async function logout() { await api.logout(); setUser(null); setStatus(null); }
  async function openPreview(id: string) { try { setPreview(await api.queuePreview(id)); } catch (reason) { setError(reason instanceof Error ? reason.message : "Prévia indisponível"); } }
  if (!user) return <Login onSuccess={(username) => { setUser(username); void refresh(); }} />;

  return <div className="min-h-screen bg-shell lg:grid lg:grid-cols-[280px_1fr]">
    <aside className="sticky top-0 z-50 border-b border-white/10 bg-slate-950 p-4 text-white lg:h-screen lg:border-b-0 lg:p-6">
      <div className="flex items-center justify-between gap-3"><div className="flex items-center gap-3"><div className="grid h-11 w-11 place-items-center rounded-2xl bg-signal text-slate-950 shadow-glow"><RadioTower /></div><div><p className="font-black tracking-tight">Money Extra</p><p className="text-xs text-white/45">Central operacional</p></div></div><button className="rounded-xl border border-white/10 p-2 lg:hidden" onClick={() => setMenuOpen((value) => !value)} aria-label="Alternar menu">{menuOpen ? <X/> : <Menu/>}</button></div>
      <div className="mt-6 rounded-2xl border border-white/10 bg-white/5 p-3"><div className="flex items-center gap-2 text-xs font-bold text-emerald-300"><span className="h-2 w-2 rounded-full bg-emerald-400 animate-pulse" />{status?.delivery_mode.replaceAll("_", " ") ?? "CARREGANDO"}</div><p className="mt-1 text-[11px] text-white/40">Revalidação ativa antes de cada envio</p></div>
      <div className={`${menuOpen ? "block" : "hidden"} lg:block`}><nav className="mt-5 grid max-h-[65vh] grid-cols-2 gap-2 overflow-y-auto sm:grid-cols-3 lg:max-h-none lg:grid-cols-1" aria-label="Navegação principal">{navigation.map(({ id, label, icon: Icon }) => <button key={id} onClick={() => { setView(id); setMenuOpen(false); }} aria-current={view === id ? "page" : undefined} className={`flex items-center gap-3 rounded-xl px-3 py-3 text-left text-sm font-semibold transition ${view === id ? "bg-white text-slate-950 shadow-lg" : "text-white/60 hover:bg-white/10 hover:text-white"}`}><Icon size={17} />{label}</button>)}</nav>
      <button onClick={logout} className="mt-5 flex items-center gap-2 text-sm text-white/50 hover:text-white"><LogOut size={16} />Sair</button></div>
    </aside>
    <main className="min-w-0 p-5 sm:p-8 xl:p-10 2xl:p-12">
      <div className="mx-auto max-w-[1600px]">
      <header className="sticky top-0 z-30 -mx-3 mb-8 flex flex-wrap items-center justify-between gap-3 rounded-2xl border border-white/80 bg-shell/90 px-3 py-3 backdrop-blur-xl"><div className="min-h-10">{error ? <p role="alert" className="notice bg-red-50 text-red-700">{error}</p> : notice ? <p role="status" className="notice bg-emerald-50 text-emerald-700">{notice}</p> : <p className="px-2 py-2.5 text-sm text-slate-500">Central operacional atualizada em tempo real</p>}</div><div className="flex flex-wrap gap-2"><button className="button-secondary" onClick={() => void refresh()} disabled={loading || actionBusy}><RefreshCw size={16} className={loading ? "animate-spin" : ""} />Atualizar</button><button className="button-secondary" onClick={() => void action(api.validatePublicChannel, "Canal público validado.")} disabled={actionBusy}><RadioTower size={16} />Validar canal</button><button className="button-primary" onClick={() => void action(api.discover, "Descoberta adicionada à fila.")} disabled={actionBusy}><Play size={16} />Descobrir ofertas</button></div></header>
      {view === "overview" && status ? <Overview status={status} opportunities={opportunities} /> : null}
      {view === "operations" ? <OperationsCenter onAction={action} /> : null}
      {view === "opportunities" ? <OpportunityGrid opportunities={opportunities} totalMonitored={status?.product_count ?? opportunities.length} totalEligible={status?.eligible_count ?? 0} /> : null}
      {view === "queue" ? <QueueTable onPreview={openPreview} onNotify={(run, message) => action(run, message)} /> : null}
      {view === "coupons" ? <CouponCenter onSync={() => action(api.syncCoupons, "Sincronização de cupons iniciada.")} /> : null}
      {view === "editorial" ? <EditorialCenter onAction={action} /> : null}
      {view === "publications" ? <PublicationHistory /> : null}
      {view === "settings" ? <SettingsPanel /> : null}
      </div>
    </main>
    {preview ? <PreviewDialog preview={preview} onClose={() => setPreview(null)} /> : null}
  </div>;
}

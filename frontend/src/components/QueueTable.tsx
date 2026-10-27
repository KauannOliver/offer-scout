import { ArrowDownUp, Eye, GripVertical, ImageOff, RefreshCw, RotateCcw, Search, Send, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";
import { api } from "../api";
import { useQueryState } from "../navigation";
import type { QueueItem, QueuePage } from "../types";
import { StatusBadge } from "./StatusBadge";

type Props = {
  onPreview: (id: string) => Promise<void>;
  onNotify: (run: () => Promise<unknown>, message: string) => Promise<void>;
};

const statusFilters = [
  ["", "Todos"], ["READY,DRY_RUN", "Prontos"], ["PUBLISHED", "Enviados"],
  ["DISCOVERED,SELECTED,LINK_PENDING,CREATIVE_PENDING,RETRY", "Pendentes"], ["FAILED", "Falhas"],
] as const;

export function QueueTable({ onPreview, onNotify }: Props) {
  const [data, setData] = useState<QueuePage>({ items: [], page: 1, page_size: 30, total: 0, counts: {} });
  const [rawPage, setRawPage] = useQueryState("queue.page", "1");
  const page = Math.max(1, Number(rawPage) || 1);
  const setPage = (value: number) => setRawPage(String(value));
  const [status, setStatus] = useQueryState("queue.status", "");
  const [sort, setSort] = useQueryState("queue.sort", "priority");
  const [search, setSearch] = useQueryState("queue.search", "");
  const [category, setCategory] = useQueryState("queue.category", "");
  const [strategy, setStrategy] = useQueryState("queue.strategy", "");
  const requestSequence = useRef(0);
  const [options, setOptions] = useState<{ categories: string[]; strategies: string[] }>({ categories: [], strategies: [] });
  const [selected, setSelected] = useState<Set<string>>(() => new Set());
  const [dragged, setDragged] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    const sequence = ++requestSequence.current;
    setLoading(true); setError("");
    try { const result = await api.queue({ page, status, sort, search, category, strategy }); if (sequence === requestSequence.current) setData(result); }
    catch (reason) { if (sequence === requestSequence.current) setError(reason instanceof Error ? reason.message : "Falha ao carregar fila"); }
    finally { if (sequence === requestSequence.current) setLoading(false); }
  }, [category, page, search, sort, status, strategy]);

  useEffect(() => { const timer = window.setTimeout(() => void load(), 250); return () => window.clearTimeout(timer); }, [load]);
  useEffect(() => { void api.queueOptions().then(setOptions); }, []);

  async function single(id: string, action: "regenerate" | "retry" | "publish-now" | "revalidate") {
    await onNotify(() => api.queueAction(id, action), "Ação enviada para processamento.");
    await load();
  }

  async function bulk(action: string) {
    const ids = [...selected];
    if (!ids.length) return;
    await onNotify(() => api.bulkQueue(ids, action), `${ids.length} item(ns) enviados para ${action}.`);
    setSelected(new Set()); await load();
  }

  async function dropOn(targetId: string) {
    if (!dragged || dragged === targetId) return;
    const items = [...data.items];
    const from = items.findIndex((item) => item.id === dragged);
    const to = items.findIndex((item) => item.id === targetId);
    const [moved] = items.splice(from, 1); items.splice(to, 0, moved);
    setData((current) => ({ ...current, items })); setDragged(null);
    try { await api.reorderQueue(items.map((item) => item.id)); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Falha ao reordenar"); await load(); }
  }

  const allSelected = data.items.length > 0 && data.items.every((item) => selected.has(item.id));
  return <section aria-labelledby="queue-title">
    <div className="flex flex-wrap items-end justify-between gap-4"><div><p className="label">Pipeline de publicação</p><h2 id="queue-title" className="page-title">Fila operacional</h2><p className="mt-2 text-slate-500">{data.total} itens no filtro atual. Arraste para priorizar e use ações individuais ou em lote.</p></div><button className="button-secondary" onClick={() => void load()}><RefreshCw size={16} className={loading ? "animate-spin" : ""}/>Atualizar</button></div>
    <div className="card mt-7 space-y-4 p-4">
      <div className="flex flex-wrap gap-2">{statusFilters.map(([value, label]) => <button key={label} className={`rounded-full px-4 py-2 text-sm font-bold transition ${status === value ? "bg-slate-950 text-white" : "bg-slate-100 text-slate-600 hover:bg-slate-200"}`} onClick={() => { setStatus(value); setPage(1); }}>{label}{value && value.indexOf(",") < 0 ? ` (${data.counts[value] ?? 0})` : ""}</button>)}</div>
      <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-[1fr_220px_220px_220px_auto]">
        <label className="relative"><span className="sr-only">Pesquisar fila</span><Search className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={16}/><input className="input pl-10" value={search} onChange={(event) => { setSearch(event.target.value); setPage(1); }} placeholder="Produto ou código MLB"/></label>
        <label className="relative"><span className="sr-only">Ordenar fila</span><ArrowDownUp className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={16}/><select className="input pl-10" value={sort} onChange={(event) => setSort(event.target.value)}><option value="priority">Prioridade manual</option><option value="score">Maior score</option><option value="discount">Maior desconto</option><option value="scheduled">Próxima publicação</option><option value="created">Mais recentes</option></select></label>
        <select className="input" value={category} onChange={(event) => { setCategory(event.target.value); setPage(1); }}><option value="">Todas as categorias</option>{options.categories.map((value) => <option key={value}>{value}</option>)}</select>
        <select className="input" value={strategy} onChange={(event) => { setStrategy(event.target.value); setPage(1); }}><option value="">Todas as estratégias</option>{options.strategies.map((value) => <option key={value}>{value.replaceAll("_", " ")}</option>)}</select>
        <label className="flex items-center gap-2 rounded-xl border border-slate-200 px-4 text-sm font-bold"><input type="checkbox" checked={allSelected} onChange={(event) => setSelected((previous) => { const next = new Set(previous); for (const item of data.items) { if (event.target.checked) next.add(item.id); else next.delete(item.id); } return next; })}/>Selecionar página</label>
      </div>
      {selected.size ? <div className="flex flex-wrap items-center gap-2 rounded-2xl bg-amber-50 p-3"><strong className="mr-2 text-sm">{selected.size} selecionado(s)</strong><button className="button-secondary" onClick={() => void bulk("revalidate")}>Revalidar</button><button className="button-secondary" onClick={() => void bulk("regenerate")}>Regenerar</button><button className="button-primary" onClick={() => void bulk("publish-now")}>Publicar</button><button className="button-secondary" onClick={() => void bulk("discard")}>Descartar</button></div> : null}
    </div>
    {error ? <p role="alert" className="mt-4 text-red-700">{error}</p> : null}
    <div className="mt-5 space-y-3">{data.items.map((item) => <QueueCard key={item.id} item={item} selected={selected.has(item.id)} onSelected={(checked) => setSelected((current) => { const next = new Set(current); if (checked) next.add(item.id); else next.delete(item.id); return next; })} onDrag={() => setDragged(item.id)} onDrop={() => void dropOn(item.id)} onPreview={onPreview} onAction={single} onDiscard={async () => { await onNotify(() => api.discard(item.id), "Oferta descartada."); await load(); }}/>) }{!loading && data.items.length === 0 ? <div className="card text-center text-slate-500">Nenhum item corresponde aos filtros.</div> : null}</div>
    <div className="mt-7 flex items-center justify-center gap-3"><button className="button-secondary" disabled={page === 1} onClick={() => setPage(Math.max(1, page - 1))}>Anterior</button><span className="metric-pill">Página <strong>{page}</strong></span><button className="button-secondary" disabled={page * data.page_size >= data.total} onClick={() => setPage(page + 1)}>Próxima</button></div>
  </section>;
}

function QueueCard({ item, selected, onSelected, onDrag, onDrop, onPreview, onAction, onDiscard }: { item: QueueItem; selected: boolean; onSelected: (value: boolean) => void; onDrag: () => void; onDrop: () => void; onPreview: (id: string) => Promise<void>; onAction: (id: string, action: "regenerate" | "retry" | "publish-now" | "revalidate") => Promise<void>; onDiscard: () => Promise<void> }) {
  const ready = item.status === "READY" || item.status === "DRY_RUN";
  const protectedState = ["PUBLISHED", "PUBLISHING"].includes(item.status);
  const operationalMessage = item.operational_reason ?? (item.status === "CREATIVE_PENDING" ? "Criativo aguardando regeneração" : null);
  const actualError = item.last_error;
  return <article draggable={!(["PUBLISHED", "PUBLISHING"].includes(item.status))} onDragStart={onDrag} onDragOver={(event) => event.preventDefault()} onDrop={onDrop} className={`card flex flex-wrap items-center gap-4 transition ${selected ? "ring-2 ring-amber-400" : ""}`}>
    <GripVertical className="cursor-grab text-slate-300" aria-label="Arrastar para reordenar"/><input type="checkbox" checked={selected} onChange={(event) => onSelected(event.target.checked)} aria-label={`Selecionar ${item.product_title}`}/>
    {item.product_image_url ? <img src={item.product_image_url} alt="" className="h-20 w-20 rounded-2xl bg-slate-50 object-contain p-1"/> : <div className="grid h-20 w-20 place-items-center rounded-2xl bg-slate-100 text-slate-400"><ImageOff size={22}/></div>}
    <div className="min-w-[230px] flex-1"><h3 className="font-bold">{item.product_title}</h3><p className="mt-1 text-xs text-slate-400">{item.product_item_id}{item.category_name ? ` • ${item.category_name}` : ""}</p><div className="mt-2 flex flex-wrap items-center gap-2"><StatusBadge value={item.status}/><span className={`badge ${item.publication_kind === "BAD_OFFER" ? "bg-rose-50 text-rose-700" : "bg-violet-50 text-violet-700"}`}>{item.publication_kind.replaceAll("_", " ")}</span>{item.last_error?.startsWith("SKIPPED_DUPLICATE") ? <span className="badge bg-amber-50 text-amber-700">Duplicidade neutralizada</span> : null}{item.source_strategy ? <span className="badge bg-blue-50 text-blue-700">{item.source_strategy.replaceAll("_", " ")}</span> : null}<span className="text-xs text-slate-500">Prioridade {item.priority}</span></div>{operationalMessage ? <p className="mt-2 text-sm text-blue-700">{operationalMessage}</p> : null}{actualError ? <p className="mt-2 text-sm text-red-700">{actualError}</p> : null}</div>
    <div className="flex flex-wrap gap-2"><Action label="Prévia" icon={Eye} onClick={() => void onPreview(item.id)} disabled={!item.status.match(/READY|DRY_RUN|PUBLISH/)}/><Action label="Revalidar" icon={RefreshCw} onClick={() => void onAction(item.id, "revalidate")} disabled={protectedState}/><Action label="Regenerar" icon={RotateCcw} onClick={() => void onAction(item.id, "regenerate")} disabled={protectedState}/><Action label="Publicar" icon={Send} onClick={() => void onAction(item.id, "publish-now")} disabled={!ready} primary/><Action label="Descartar" icon={Trash2} onClick={() => void onDiscard()} disabled={protectedState || item.status === "SKIPPED"}/></div>
  </article>;
}

function Action({ label, icon: Icon, onClick, disabled, primary }: { label: string; icon: typeof Eye; onClick: () => void; disabled?: boolean; primary?: boolean }) { return <button className={primary ? "button-primary" : "button-secondary"} onClick={onClick} disabled={disabled}><Icon size={15}/>{label}</button>; }

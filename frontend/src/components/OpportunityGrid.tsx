import { ArrowDownUp, ExternalLink, Search, Sparkles, TicketCheck, X } from "lucide-react";
import { useDeferredValue, useEffect, useState } from "react";
import { api } from "../api";
import { Product360 } from "./Product360";
import { useQueryState } from "../navigation";
import type { Opportunity } from "../types";
import { StatusBadge } from "./StatusBadge";

const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
type Sort = "score" | "discount" | "price";

function numeric(value: string | null) {
  const parsed = Number(value);
  return Number.isFinite(parsed) ? parsed : 0;
}

export function OpportunityGrid({ opportunities, totalMonitored, totalEligible }: { opportunities: Opportunity[]; totalMonitored: number; totalEligible: number }) {
  const [rawPage, setPage] = useQueryState("offers.page", "1");
  const page = Math.max(1, Number(rawPage) || 1);
  const onPage = (value: number) => setPage(String(value));
  const [query, setQuery] = useQueryState("offers.search", "");
  const [productId, setProductId] = useState<string | null>(null);
  const [error, setError] = useState("");
  const [onlyEligible, setOnlyEligible] = useState(false);
  const [sort, setSort] = useQueryState("offers.sort", "score");
  const deferredQuery = useDeferredValue(query.trim());
  const [visible, setVisible] = useState(opportunities);
  const [total, setTotal] = useState(totalMonitored);
  const [loading, setLoading] = useState(false);
  useEffect(() => {
    let active = true;
    const timer = window.setTimeout(() => {
      setLoading(true);
      setError("");
      void api.opportunityPage({ page, search: deferredQuery || undefined, sort, eligible: onlyEligible ? true : undefined })
        .then((result) => { if (active) { setVisible(result.items); setTotal(result.total); } })
        .catch((reason: Error) => { if (active) setError(reason.message); })
        .finally(() => { if (active) setLoading(false); });
    }, 250);
    return () => { active = false; window.clearTimeout(timer); };
  }, [deferredQuery, onlyEligible, page, sort]);

  return <section aria-labelledby="opportunities-title">
    {productId ? <Product360 id={productId} onClose={() => setProductId(null)} /> : null}
    {error ? <p className="notice bg-red-50 text-red-700" role="alert">{error}</p> : null}
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div><p className="label">Seleção inteligente</p><h2 id="opportunities-title" className="page-title">Oportunidades</h2><p className="mt-2 text-sm text-slate-500">Compare os sinais da oferta antes de colocá-la na fila.</p></div>
      <div className="flex gap-2"><span className="metric-pill"><strong>{totalMonitored}</strong> monitoradas</span><span className="metric-pill text-emerald-700"><strong>{totalEligible}</strong> elegíveis</span></div>
    </div>
    <div className="card mt-6 flex flex-col gap-3 p-4 lg:flex-row lg:items-center">
      <label className="relative min-w-0 flex-1"><span className="sr-only">Buscar produto</span><Search className="absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={17} /><input value={query} onChange={(event) => { setQuery(event.target.value); onPage(1); }} placeholder="Buscar por nome do produto" className="input pl-10 pr-10" />{query ? <button type="button" onClick={() => { setQuery(""); onPage(1); }} aria-label="Limpar busca" className="absolute right-3 top-1/2 -translate-y-1/2 text-slate-400 hover:text-slate-950"><X size={16} /></button> : null}</label>
      <label className="relative"><span className="sr-only">Ordenar oportunidades</span><ArrowDownUp className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" size={16} /><select className="input min-w-52 pl-10" value={sort} onChange={(event) => { setSort(event.target.value as Sort); onPage(1); }}><option value="score">Maior score</option><option value="discount">Maior desconto</option><option value="price">Menor preço</option></select></label>
      <button type="button" className={onlyEligible ? "button-primary" : "button-secondary"} aria-pressed={onlyEligible} onClick={() => { setOnlyEligible((value) => !value); onPage(1); }}><Sparkles size={16} />Só elegíveis</button>
    </div>
    <p className="mt-4 text-sm text-slate-500" aria-live="polite">{loading ? "Atualizando resultados…" : <>Exibindo <strong className="text-slate-800">{visible.length}</strong> nesta página, de <strong className="text-slate-800">{total}</strong> resultado(s)</>}</p>
    <div className="mt-5 grid gap-5 md:grid-cols-2 2xl:grid-cols-3">
      {visible.map((item) => {
        const savings = item.previous_price && item.price ? Math.max(0, numeric(item.previous_price) - numeric(item.price)) : 0;
        return <article key={item.id} className="card group isolate grid min-w-0 grid-rows-[auto_1fr] overflow-hidden p-0 transition duration-200 hover:-translate-y-0.5 hover:shadow-xl">
          <div className="relative aspect-[4/3] min-h-0 w-full overflow-hidden border-b border-slate-100 bg-gradient-to-b from-slate-50 to-white">
            {item.image_url ? <img src={item.image_url} alt={item.title} loading="lazy" className="absolute inset-0 h-full w-full object-contain p-6 transition duration-300 group-hover:scale-[1.03]" /> : <div className="absolute inset-0 grid place-items-center text-sm text-slate-400">Imagem indisponível</div>}
            <span className="absolute right-4 top-4 z-10 rounded-full bg-slate-950/95 px-3 py-1.5 text-xs font-black text-white shadow-lg">SCORE {item.score ?? "—"}</span>
          </div>
          <div className="relative z-10 flex min-w-0 flex-col bg-white p-5">
            <button className="mb-3 self-start text-xs font-bold text-amber-800 underline" onClick={() => setProductId(item.id)}>Produto 360 · ver histórico e decisões</button>
            <div className="flex min-w-0 items-start justify-between gap-3"><h3 className="line-clamp-2 min-w-0 flex-1 font-bold leading-snug text-slate-950">{item.title}</h3><a href={item.product_url} target="_blank" rel="noreferrer" aria-label={`Abrir ${item.title} no Mercado Livre`} className="shrink-0 rounded-lg p-2 text-slate-400 transition hover:bg-slate-100 hover:text-slate-950"><ExternalLink size={17} /></a></div>
            <div className="mt-5 flex flex-wrap items-end gap-x-3 gap-y-1"><strong className="text-2xl tracking-tight text-slate-950">{item.price ? money.format(numeric(item.price)) : "—"}</strong>{item.previous_price ? <s className="pb-1 text-sm text-slate-400">{money.format(numeric(item.previous_price))}</s> : null}</div>
            {savings > 0 ? <p className="mt-1 text-xs font-semibold text-emerald-700">Economia de {money.format(savings)}</p> : null}
            <div className="mt-4 flex flex-wrap gap-2"><StatusBadge value={item.queue_status ?? item.classification ?? "MONITORADO"} />{item.discount_percent ? <span className="badge bg-emerald-50 text-emerald-700">{numeric(item.discount_percent).toFixed(0)}% OFF</span> : null}{item.coupon?.status === "APPLICABLE" ? <span className="badge bg-amber-50 text-amber-800"><TicketCheck size={13} />Cupom {item.coupon.code ?? "a gerar"}</span> : null}</div>
            <div className="mt-auto border-t border-slate-100 pt-4"><p className="text-xs text-slate-500">⭐ {item.rating ?? "—"} <span aria-hidden="true">•</span> {item.sold_text ?? "Vendas não informadas"}</p>{item.coupon?.reason ? <p className="mt-2 line-clamp-2 text-xs text-slate-500" title={item.coupon.reason}>Cupom: {item.coupon.reason}</p> : null}</div>
            <p className="mt-2 text-xs text-slate-400">{item.category_name ?? "Categoria em identificação"} • confiança do preço {Math.round((item.price_metrics?.price_confidence ?? 0.6) * 100)}%</p>
          </div>
        </article>;
      })}
      {visible.length === 0 ? <div className="card grid min-h-60 place-items-center text-center text-slate-500 md:col-span-2 2xl:col-span-3"><div><Search className="mx-auto mb-3 text-slate-300" size={32} /><p className="font-semibold text-slate-700">Nenhuma oportunidade encontrada</p><p className="mt-1 text-sm">Ajuste a busca ou remova o filtro de elegibilidade.</p></div></div> : null}
    </div>
    <div className="mt-7 flex items-center justify-center gap-3"><button className="button-secondary" disabled={page === 1} onClick={() => onPage(Math.max(1, page - 1))}>Anterior</button><span className="metric-pill">Página <strong>{page}</strong> de <strong>{Math.max(1, Math.ceil(total / 30))}</strong></span><button className="button-secondary" disabled={page * 30 >= total} onClick={() => onPage(page + 1)}>Próxima</button></div>
  </section>;
}

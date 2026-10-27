import { ChevronDown, ChevronLeft, ChevronRight, Clock3, ExternalLink, PackageCheck, RefreshCw, Send, ShieldCheck, TicketCheck } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import { api } from "../api";
import type { CouponProductPage, CouponRecord } from "../types";

const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });
const dateTime = new Intl.DateTimeFormat("pt-BR", { dateStyle: "short", timeStyle: "short" });

export function CouponCenter({ onSync }: { onSync: () => Promise<void> }) {
  const [records, setRecords] = useState<CouponRecord[]>([]);
  const [expanded, setExpanded] = useState<string | null>(null);
  const [products, setProducts] = useState<Record<string, CouponProductPage>>({});
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [source, setSource] = useState("");
  const [status, setStatus] = useState("");
  const [search, setSearch] = useState("");
  const load = useCallback(() => api.coupons({ source, status, search }).then(setRecords).catch((reason: Error) => setError(reason.message)), [search, source, status]);
  useEffect(() => { const timer = window.setTimeout(() => void load(), 200); return () => window.clearTimeout(timer); }, [load]);

  async function couponAction(action: () => Promise<unknown>, message: string) {
    setError(""); setNotice("");
    try { await action(); setNotice(message); setTimeout(() => void load(), 1200); }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Falha na ação"); }
  }

  async function toggleProducts(id: string, page = 1) {
    if (expanded === id && page === 1) { setExpanded(null); return; }
    setExpanded(id);
    try {
      const result = await api.couponProducts(id, page);
      setProducts((current) => ({ ...current, [id]: result }));
    }
    catch (reason) { setError(reason instanceof Error ? reason.message : "Falha ao carregar produtos"); }
  }

  return <section aria-labelledby="coupon-title">
    <div className="flex flex-wrap items-end justify-between gap-4">
      <div><p className="label">Fonte oficial Mercado Livre</p><h2 id="coupon-title" className="page-title">Central de cupons</h2><p className="mt-2 text-slate-500">Códigos, regras e produtos são verificados na área autenticada antes da publicação.</p></div>
      <button className="button-primary" onClick={() => void onSync().then(load)}><RefreshCw size={16} />Sincronizar agora</button>
    </div>
    {error ? <p role="alert" className="mt-4 text-red-700">{error}</p> : notice ? <p role="status" className="mt-4 text-emerald-700">{notice}</p> : null}
    <div className="card mt-6 grid gap-3 p-4 sm:grid-cols-3"><input className="input" value={search} onChange={(event) => setSearch(event.target.value)} placeholder="Buscar código ou campanha"/><select className="input" value={source} onChange={(event) => setSource(event.target.value)}><option value="">Todas as fontes</option><option value="affiliate_generated">Códigos gerados</option><option value="marketplace_general">Cupons Mercado Livre</option></select><select className="input" value={status} onChange={(event) => setStatus(event.target.value)}><option value="">Todos os estados</option><option value="ACTIVE">Ativos</option><option value="EXPIRED">Expirados</option><option value="EXHAUSTED">Esgotados</option></select></div>
    <div className="mt-8 grid gap-5 xl:grid-cols-2">
      {records.map((coupon) => {
        const productPage = products[coupon.id];
        return <article key={coupon.id} className="card overflow-hidden">
          <div className="flex items-start justify-between gap-3"><span className="rounded-2xl bg-amber-50 p-3 text-amber-800"><TicketCheck /></span><div className="flex flex-wrap justify-end gap-2"><span className="badge bg-blue-50 text-blue-700">{coupon.source_kind === "affiliate_generated" ? "CÓDIGO GERADO" : "MERCADO LIVRE"}</span><span className={`badge ${coupon.shareable ? "bg-emerald-50 text-emerald-700" : "bg-slate-100 text-slate-600"}`}>{coupon.shareable ? "COMPARTILHÁVEL" : "INFORMATIVO"}</span></div></div>
          <h3 className="mt-5 text-lg font-black text-slate-950">{coupon.title}</h3>
          <p className="mt-1 text-sm text-slate-500">{coupon.scope ?? "Escopo não informado"}{coupon.official_coupon_id ? ` • ID ${coupon.official_coupon_id}` : ""}</p>
          <div className="mt-4 rounded-2xl bg-slate-950 p-4 text-white"><p className="text-xs uppercase tracking-widest text-white/50">Código para divulgação</p><p className="mt-1 break-all font-mono text-xl font-black text-signal">{coupon.code ?? "NÃO DISPONÍVEL"}</p></div>
          <dl className="mt-4 grid grid-cols-2 gap-3 text-sm">
            <Fact label="Desconto" value={coupon.discount_percent ? `${Number(coupon.discount_percent)}%` : coupon.discount_amount ? money.format(Number(coupon.discount_amount)) : "—"} />
            <Fact label="Compra mínima" value={coupon.minimum_purchase ? money.format(Number(coupon.minimum_purchase)) : "—"} />
            <Fact label="Limite" value={coupon.maximum_discount ? money.format(Number(coupon.maximum_discount)) : "—"} />
            <Fact label="Produtos aplicáveis" value={String(coupon.applicable_products)} />
          </dl>
          <div className="mt-4 grid gap-2 rounded-2xl bg-slate-50 p-4 text-xs text-slate-600">
            {coupon.expires_at ? <p className="flex items-center gap-2"><Clock3 size={14} />Expira em {dateTime.format(new Date(coupon.expires_at))}</p> : null}
            {coupon.remaining_budget ? <p>{coupon.remaining_budget}</p> : null}
            {coupon.last_verified_at ? <p>Verificado em {dateTime.format(new Date(coupon.last_verified_at))}</p> : null}
          </div>
          {coupon.restrictions.length ? <p className="mt-4 text-xs leading-5 text-slate-500">{coupon.restrictions.join(" • ")}</p> : null}
          <div className="mt-5 flex flex-wrap gap-2">
            <button className="button-secondary" onClick={() => void couponAction(() => api.revalidateCoupon(coupon.id), "Revalidação adicionada à fila.")}><ShieldCheck size={15}/>Revalidar</button>
            <button className="button-primary" disabled={!coupon.code || !coupon.shareable || coupon.lifecycle_status !== "ACTIVE"} onClick={() => void couponAction(() => api.publishCoupon(coupon.id), "Publicação adicionada à fila.")}><Send size={15}/>Publicar no canal</button>
            <button className="button-secondary" disabled={!coupon.applicable_products} onClick={() => void toggleProducts(coupon.id)}><PackageCheck size={15}/>Ver produtos <ChevronDown size={14}/></button>
          </div>
          {expanded === coupon.id && productPage ? <div className="mt-5 border-t border-slate-100 pt-5">
            <div className="mb-3 flex items-center justify-between"><p className="font-bold">Produtos confirmados ({productPage.total})</p><p className="text-xs text-slate-400">Página {productPage.page}</p></div>
            <div className="grid gap-3 sm:grid-cols-2">{productPage.items.map((product) => <div key={product.id} className="flex min-w-0 gap-3 rounded-2xl border border-slate-100 p-3">
              <div className="h-16 w-16 shrink-0 overflow-hidden rounded-xl bg-white">{product.image_url ? <img className="h-full w-full object-contain" src={product.image_url} alt="" /> : <div className="grid h-full place-items-center text-xs text-slate-300">Sem foto</div>}</div>
              <div className="min-w-0"><p className="line-clamp-2 text-sm font-bold">{product.title}</p><p className="mt-1 text-xs text-slate-500">{product.price ? money.format(Number(product.price)) : "Preço a revalidar"}</p><a className="mt-1 inline-flex items-center gap-1 text-xs font-bold text-blue-600" href={product.product_url} target="_blank" rel="noreferrer">Abrir produto <ExternalLink size={11}/></a></div>
            </div>)}</div>
            <div className="mt-4 flex justify-end gap-2"><button className="button-secondary" disabled={productPage.page <= 1} onClick={() => void toggleProducts(coupon.id, productPage.page - 1)}><ChevronLeft size={15}/>Anterior</button><button className="button-secondary" disabled={productPage.page * productPage.page_size >= productPage.total} onClick={() => void toggleProducts(coupon.id, productPage.page + 1)}>Próxima<ChevronRight size={15}/></button></div>
          </div> : null}
        </article>;
      })}
      {records.length === 0 ? <div className="card text-center text-slate-500 xl:col-span-2">Nenhum cupom sincronizado ainda.</div> : null}
    </div>
  </section>;
}

function Fact({ label, value }: { label: string; value: string }) { return <div><dt className="text-slate-400">{label}</dt><dd className="mt-1 font-bold">{value}</dd></div>; }

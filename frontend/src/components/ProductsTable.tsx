import type { Product } from "../types";

const money = new Intl.NumberFormat("pt-BR", { style: "currency", currency: "BRL" });

export function ProductsTable({ products }: { products: Product[] }) {
  return (
    <section aria-labelledby="products-title">
      <p className="label">Catálogo normalizado</p><h2 id="products-title" className="mt-2 text-4xl font-black tracking-tight">Produtos</h2>
      <div className="card mt-8 overflow-x-auto p-0">
        <table className="w-full min-w-[760px] text-left">
          <thead className="border-b border-black/5 bg-slate-50 text-xs uppercase tracking-wider text-slate-500"><tr><th className="p-5">Produto</th><th className="p-5">Preço</th><th className="p-5">Desconto</th><th className="p-5">Avaliação</th><th className="p-5">Vendas</th></tr></thead>
          <tbody>{products.map((product) => { const snapshot = product.snapshots.at(-1); return (
            <tr key={product.id} className="border-b border-black/5 last:border-0"><td className="p-5"><a className="font-bold hover:underline" href={product.product_url} target="_blank" rel="noreferrer">{product.title}</a><p className="mt-1 text-xs text-slate-500">{product.item_id}</p></td><td className="p-5 font-bold">{snapshot?.price ? money.format(Number(snapshot.price)) : "—"}</td><td className="p-5">{snapshot?.discount_percent ? `${Number(snapshot.discount_percent).toFixed(0)}%` : "—"}</td><td className="p-5">{snapshot?.rating ?? "—"}</td><td className="p-5">{snapshot?.sold_text ?? "—"}</td></tr>
          ); })}</tbody>
        </table>
        {products.length === 0 ? <p className="p-8 text-center text-slate-500">Nenhum produto coletado ainda.</p> : null}
      </div>
    </section>
  );
}

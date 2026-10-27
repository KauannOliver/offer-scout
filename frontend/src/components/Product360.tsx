import { useEffect, useRef, useState } from "react";
import { X } from "lucide-react";
import { api } from "../api";
import type { ProductOverview } from "../types";

const money = (value: string | null) => value === null ? "Não informado" : Number(value).toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
const date = (value: string | null) => value ? new Date(value).toLocaleString("pt-BR") : "Não verificado";

export function Product360({ id, onClose }: { id: string; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [data, setData] = useState<ProductOverview | null>(null);
  const [page, setPage] = useState(1);
  const [error, setError] = useState("");
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const element = dialog.current;
    element?.showModal();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { element?.close(); document.body.style.overflow = overflow; previous?.focus(); };
  }, []);
  useEffect(() => {
    let active = true;
    api.productOverview(id, page).then((value) => { if (active) setData(value); })
      .catch((reason: Error) => { if (active) setError(reason.message); });
    return () => { active = false; };
  }, [id, page]);
  return <dialog ref={dialog} className="preview-dialog" onCancel={(event) => { event.preventDefault(); onClose(); }} aria-labelledby="product360-title">
    <div className="flex h-full flex-col"><header className="flex items-center justify-between border-b p-5"><div><p className="label">Evidências e decisões</p><h2 id="product360-title" className="text-xl font-bold">Produto 360</h2></div><button className="button-secondary" onClick={onClose} aria-label="Fechar produto"><X size={20}/></button></header>
      <div className="min-h-0 flex-1 space-y-5 overflow-y-auto bg-slate-50 p-5">
        {error ? <p role="alert">{error}</p> : !data ? <p role="status">Carregando histórico…</p> : <>
          <div className="card flex flex-col gap-5 sm:flex-row">{data.image_url ? <img src={data.image_url} alt="" className="h-40 w-full object-contain sm:h-36 sm:w-36"/> : null}<div className="min-w-0 flex-1"><h3 className="break-words text-xl font-bold">{data.title}</h3><p className="mt-2 text-sm text-slate-500">{data.category ?? "Sem categoria"} • {data.brand ?? "Marca não informada"}</p><p className="mt-3 text-sm">Última verificação: {date(data.last_verified_at)}</p><p className="text-sm">{data.observations} verificações detalhadas registradas</p></div></div>
          <div className="grid gap-5 md:grid-cols-2"><section className="card"><h3 className="font-bold">Por que esta oferta?</h3><p className="my-3 text-3xl font-black">{data.score?.total ?? "—"}<span className="text-sm font-normal"> / 100</span></p><p>{data.score?.eligible ? "Atende à seleção atual. A publicação ainda depende da revalidação e dos limites." : "Não atende à seleção atual."}</p><ul className="mt-3 space-y-1 text-sm text-slate-600">{data.score?.reasons.map((reason) => <li key={reason}>{reason.replaceAll("_", " ")}</li>)}</ul><div className="mt-4 space-y-2">{Object.entries(data.score?.components ?? {}).map(([name, value]) => <div key={name} className="flex justify-between text-xs"><span>{name.replaceAll("_", " ")}</span><strong>{value === null ? "Sem dado" : `${Math.round(value * 100)}%`}</strong></div>)}</div></section>
          <section className="card"><h3 className="font-bold">Histórico de alterações de preço</h3><p className="mt-1 text-xs text-slate-500">Snapshots não são contagem de consultas.</p><div className="mt-3 max-h-72 overflow-y-auto">{data.history.items.map((item, index) => <div key={index} className="flex justify-between gap-3 border-b py-2 text-xs"><span>{date(item.observed_at)}</span><strong>{money(item.price)}</strong></div>)}</div><div className="mt-3 flex justify-between"><button className="button-secondary" disabled={page === 1} onClick={() => setPage(page-1)}>Anterior</button><button className="button-secondary" disabled={page*30 >= data.history.total} onClick={() => setPage(page+1)}>Próxima</button></div></section></div>
          <section className="card"><h3 className="font-bold">Fila e impedimentos</h3>{data.queue.length ? data.queue.map((item, index) => <div key={index} className="mt-2 rounded-xl bg-slate-50 p-3 text-sm"><strong>{item.status}</strong><p>{item.reason ?? item.error ?? "Sem impedimento registrado"}</p></div>) : <p className="mt-2 text-sm">Produto ainda sem entrada na fila.</p>}</section>
          <section className="card"><h3 className="font-bold">Cupons e evidências oficiais</h3>{data.coupons.map((coupon, index) => <p key={index} className="mt-2 text-sm">{coupon.code ?? "Sem código"} • {coupon.status} • {coupon.reason}</p>)}{!data.coupons.length ? <p className="mt-2 text-sm">Sem associação confirmada.</p> : null}</section>
          <section className="card"><h3 className="font-bold">Publicações</h3>{data.publications.map((publication, index) => <p key={index} className="mt-2 text-sm">{date(publication.published_at)} • {publication.delivery_status} {publication.url ? <a className="underline" href={publication.url} target="_blank" rel="noreferrer">Abrir no canal</a> : null}</p>)}</section>
        </>}
      </div><footer className="border-t p-4 text-right"><button className="button-primary" onClick={onClose}>Fechar detalhes</button></footer>
    </div>
  </dialog>;
}

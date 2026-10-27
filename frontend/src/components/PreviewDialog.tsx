import { useEffect, useRef } from "react";
import { X } from "lucide-react";
import type { CreativePreview } from "../types";

export function PreviewDialog({ preview, onClose }: { preview: CreativePreview; onClose: () => void }) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    const element = dialog.current;
    element?.showModal();
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => { element?.close(); document.body.style.overflow = overflow; previous?.focus(); };
  }, []);
  return <dialog ref={dialog} onCancel={(event) => { event.preventDefault(); onClose(); }} aria-labelledby="preview-title" className="preview-dialog">
    <div className="flex h-full flex-col">
      <header className="flex shrink-0 items-center justify-between border-b border-slate-100 px-5 py-4">
        <div><p className="label">Publicação completa</p><h2 id="preview-title" className="text-xl font-bold">Prévia no Telegram</h2></div>
        <button onClick={onClose} className="button-secondary" aria-label="Fechar prévia"><X size={20}/></button>
      </header>
      <div className="min-h-0 flex-1 overflow-y-auto bg-slate-50 p-4 sm:p-7">
        <article className="mx-auto grid max-w-4xl overflow-hidden rounded-2xl border border-slate-200 bg-white lg:grid-cols-2">
          {preview.image_url ? <div className="grid place-items-center p-5"><img src={preview.image_url} alt="Produto da oferta" className="h-auto max-h-[60vh] w-full object-contain"/></div> : null}
          <div className="min-w-0 whitespace-pre-wrap break-words p-5 text-[15px] leading-relaxed" dangerouslySetInnerHTML={{ __html: preview.caption_html }}/>
        </article>
      </div>
      <footer className="flex shrink-0 items-center justify-between gap-3 border-t border-slate-100 px-5 py-4 text-xs text-slate-500"><span>{preview.generator} • {preview.template_version}</span><button className="button-primary" onClick={onClose}>Concluir</button></footer>
    </div>
  </dialog>;
}

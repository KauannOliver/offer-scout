import { useState, type FormEvent } from "react";
import { LockKeyhole } from "lucide-react";
import { api } from "../api";

type LoginProps = { onSuccess: (username: string) => void };

export function Login({ onSuccess }: LoginProps) {
  const [username, setUsername] = useState("admin");
  const [password, setPassword] = useState("");
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setLoading(true);
    setError("");
    try {
      const result = await api.login(username, password);
      onSuccess(result.username);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "Não foi possível entrar");
    } finally {
      setLoading(false);
    }
  }

  return (
    <main className="grid min-h-screen place-items-center bg-cream p-6">
      <section className="card w-full max-w-md" aria-labelledby="login-title">
        <div className="mb-8 flex h-14 w-14 items-center justify-center rounded-2xl bg-signal">
          <LockKeyhole aria-hidden="true" />
        </div>
        <p className="label">Acesso local protegido</p>
        <h1 id="login-title" className="mt-2 text-3xl font-black tracking-tight">Money Extra</h1>
        <p className="mt-2 text-slate-600">Entre para administrar o motor de ofertas.</p>
        <form onSubmit={submit} className="mt-8 space-y-5">
          <label className="block">
            <span className="label">Usuário</span>
            <input className="mt-2 w-full rounded-xl border border-black/10 px-4 py-3" value={username} onChange={(event) => setUsername(event.target.value)} autoComplete="username" required />
          </label>
          <label className="block">
            <span className="label">Senha</span>
            <input className="mt-2 w-full rounded-xl border border-black/10 px-4 py-3" type="password" value={password} onChange={(event) => setPassword(event.target.value)} autoComplete="current-password" required />
          </label>
          {error ? <p role="alert" className="rounded-xl bg-red-50 p-3 text-sm text-danger">{error}</p> : null}
          <button className="button-primary w-full" disabled={loading}>{loading ? "Entrando…" : "Entrar"}</button>
        </form>
      </section>
    </main>
  );
}

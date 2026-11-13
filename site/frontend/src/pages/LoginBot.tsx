import { Card } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Button } from "@/components/ui/button";
import { FormEvent, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api, json } from "../api";
import { SkeletonText } from "../Skeletons";
import { toast } from "../Toaster";

type Config = { configured: boolean; username: string };

export default function LoginBot() {
  const [token, setToken] = useState("");
  const qc = useQueryClient();
  const config = useQuery({ queryKey: ["login-bot"], queryFn: () => api<Config>("/api/login-bot") });
  const save = useMutation({
    mutationFn: () => api<Config>("/api/login-bot", json("PUT", { token })),
    onSuccess: (result) => { setToken(""); qc.setQueryData(["login-bot"], result); toast("Бот подключён, настройка применяется автоматически"); },
  });
  const submit = (e: FormEvent) => { e.preventDefault(); save.mutate(); };
  return <div className="page">
    <h1>Бот входа</h1>
    <p>Бот подтверждает вход сотрудников и отправляет им уведомления.</p>
    <Card className="card">
      {config.isLoading ? <SkeletonText lines={2} /> : config.isError ? <p className="error">Не удалось загрузить настройки.</p> :
        <p>{config.data?.configured ? <>Подключён: {config.data.username ? `@${config.data.username}` : "имя недоступно"}</> : "Бот ещё не подключён"}</p>}
      <form className="login-bot-form" onSubmit={submit}>
        <label>Токен бота от BotFather
          <Input type="password" autoComplete="off" value={token} onChange={e => setToken(e.target.value)} placeholder="Новый токен" />
        </label>
        <Button type="submit" disabled={!token.trim() || save.isPending}>{save.isPending ? "Проверяем…" : "Сохранить"}</Button>
        {save.isError && <p className="error">{save.error.message}</p>}
      </form>
    </Card>
  </div>;
}

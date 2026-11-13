import { Tabs, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { Table, TableHeader, TableBody, TableRow, TableHead, TableCell } from "@/components/ui/table";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, fmtRub, Me } from "../api";
import { fmtDuration } from "../Shift";
import { TableSkeletonRows } from "../Skeletons";

type Period = "day" | "week" | "month";

type LeaderRow = {
  rank: number; staff_id: number; name: string; role: string; role_label: string; is_me: boolean;
  on_shift: boolean; score: number; messages: number; new_clients: number;
  orders: number; orders_kop: number; reviews: number; closed: number;
  answer_sec: number | null; worked_sec: number;
};
type Board = {
  period: Period; from: string; to: string;
  weights: { message: number; new_client: number; order: number; review: number };
  rows: LeaderRow[];
  totals: { messages: number; new_clients: number; orders: number; reviews: number; closed: number; worked_sec: number };
};

const PERIODS: [Period, string][] = [["day", "За день"], ["week", "За неделю"], ["month", "За месяц"]];
const MEDALS = ["🥇", "🥈", "🥉"];
// "1 балл / 2 балла / 5 баллов" — Russian needs the case, not just the number.
const word = (n: number, one: string, few: string, many: string) => {
  const mod10 = Math.abs(n) % 10;
  const mod100 = Math.abs(n) % 100;
  if (mod10 === 1 && mod100 !== 11) return one;
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return few;
  return many;
};

function Podium({ rows }: { rows: LeaderRow[] }) {
  if (!rows.length) return null;
  return (
    <div className="leader-podium">
      {rows.slice(0, 3).map((row, index) => (
        <div key={row.staff_id} className={`leader-card${index === 0 ? " first" : ""}`}>
          <div className="leader-medal" aria-hidden="true">{MEDALS[index]}</div>
          <div className="leader-name">{row.name}{row.is_me && <span className="badge ok">вы</span>}</div>
          <div className="leader-score">{row.score} <small>{word(row.score, "балл", "балла", "баллов")}</small></div>
          <div className="leader-sub">
            {row.messages} {word(row.messages, "сообщение", "сообщения", "сообщений")} ·{" "}
            {row.new_clients} {word(row.new_clients, "новый клиент", "новых клиента", "новых клиентов")} ·{" "}
            {row.orders} {word(row.orders, "заказ", "заказа", "заказов")}
            {row.reviews ? ` · ${row.reviews} ${word(row.reviews, "отзыв", "отзыва", "отзывов")}` : ""}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function Leaders({ me }: { me: Me }) {
  const [period, setPeriod] = useState<Period>("day");
  const q = useQuery({
    queryKey: ["leaderboard", period],
    queryFn: () => api<Board>(`/api/support/leaderboard?period=${period}`),
    refetchInterval: 30_000,
  });
  const board = q.data;
  const rows = board?.rows ?? [];
  return (
    <>
      <h1>Лидеры</h1>
      <Tabs value={period} onValueChange={(value) => setPeriod(value as Period)}>
        <TabsList className="app-tabs">
          {PERIODS.map(([id, label]) => <TabsTrigger key={id} value={id}>{label}</TabsTrigger>)}
        </TabsList>
      </Tabs>
      <p className="muted">
        Общий рейтинг поддержки за текущие день, неделю и месяц (МСК). Данные обновляются каждые 30 секунд.
      </p>
      {q.error && <p className="error">{(q.error as Error).message}</p>}
      {q.isLoading && <Table><TableBody><TableSkeletonRows rows={5} cols={9} /></TableBody></Table>}
      {board && (
        <>
          <Podium rows={rows} />
          <Table>
            <TableHeader><TableRow>
              <TableHead>#</TableHead><TableHead>Сотрудник</TableHead><TableHead>Баллы</TableHead>
              <TableHead>Сообщения</TableHead><TableHead>Новые клиенты</TableHead><TableHead>Заказы</TableHead>
              <TableHead>Отзывы</TableHead><TableHead>Закрыто</TableHead><TableHead>Первый ответ</TableHead>
              <TableHead>Время на смене</TableHead>
            </TableRow></TableHeader>
            <TableBody>
              {rows.map((row) => (
                <TableRow key={row.staff_id} className={row.is_me ? "leader-me" : undefined}>
                  <TableCell>{MEDALS[row.rank - 1] ?? row.rank}</TableCell>
                  <TableCell>
                    {row.name} {row.is_me && <span className="badge ok">вы</span>}
                    <div className="muted">{row.role_label}</div>
                  </TableCell>
                  <TableCell><b>{row.score}</b></TableCell>
                  <TableCell>{row.messages}</TableCell>
                  <TableCell>{row.new_clients}</TableCell>
                  <TableCell>
                    {row.orders || "—"}
                    {row.orders_kop > 0 && <div className="muted">{fmtRub(row.orders_kop)}</div>}
                  </TableCell>
                  <TableCell>{row.reviews || "—"}</TableCell>
                  <TableCell>{row.closed || "—"}</TableCell>
                  <TableCell>{row.answer_sec === null ? "—" : fmtDuration(row.answer_sec)}</TableCell>
                  <TableCell>
                    {row.on_shift
                      ? <span className="badge ok">{fmtDuration(row.worked_sec)}</span>
                      : <span className="muted">{row.worked_sec ? fmtDuration(row.worked_sec) : "—"}</span>}
                  </TableCell>
                </TableRow>
              ))}
              {!rows.length && <TableRow><TableCell colSpan={10} className="muted">За этот период активности ещё нет</TableCell></TableRow>}
            </TableBody>
          </Table>
          <p className="muted">
            Всего: {board.totals.messages} {word(board.totals.messages, "сообщение", "сообщения", "сообщений")} ·{" "}
            {board.totals.new_clients} {word(board.totals.new_clients, "новый клиент", "новых клиента", "новых клиентов")} ·{" "}
            {board.totals.orders} {word(board.totals.orders, "оплаченный заказ", "оплаченных заказа", "оплаченных заказов")} ·{" "}
            {board.totals.reviews} {word(board.totals.reviews, "отзыв", "отзыва", "отзывов")} ·{" "}
            {board.totals.closed} {word(board.totals.closed, "диалог закрыт", "диалога закрыто", "диалогов закрыто")}.
          </p>
          <p className="muted">
            Баллы: сообщение — {board.weights.message}, новый клиент — {board.weights.new_client},
            оплаченный заказ — {board.weights.order}, отзыв на максимальную оценку — {board.weights.review}.
            Это рейтинг, а не деньги: вознаграждение считается в разделе «Финансы».
          </p>
        </>
      )}
      <p className="muted">
        {me.on_shift ? "Ваша смена идёт — результаты попадают в рейтинг сразу." : "Начните смену, чтобы время работы учитывалось в рейтинге."}
      </p>
    </>
  );
}
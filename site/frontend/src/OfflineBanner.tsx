import { useQueryClient } from "@tanstack/react-query";
import { setOffline, useOffline } from "./net";

/** Shown when a request failed at the network level, with a manual retry. */
export function OfflineBanner() {
  const offline = useOffline();
  const qc = useQueryClient();
  if (!offline) return null;
  return (
    <div className="offline-banner" role="status">
      <img src="/cat.png" alt="" width={20} height={20} style={{ objectFit: "contain" }} />
      <span>Нет связи · данные могли устареть</span>
      <button
        type="button"
        onClick={() => { setOffline(false); void qc.invalidateQueries(); }}
      >
        Повторить
      </button>
    </div>
  );
}

/** Ghép nối điện thoại bằng mã QR (IC-FR-03, Flow B): QR dùng một lần 5 phút, chọn mạng (Wi-Fi / hotspot),
 * hướng dẫn chấp nhận chứng chỉ, gợi ý hotspot khi không kết nối được sau 10 giây (IC-E-02). */
import { Battery, ExternalLink, Lock, Mic, RotateCw, ShieldCheck, Smartphone, TriangleAlert, Wifi } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { api } from "../api/client";
import type { CompanionStatus, PairInfo } from "../api/types";
import { useT, type MessageKey } from "../i18n";
import { errorMessage } from "../store/app";
import { Badge, Button, Modal, Spinner, inputCls } from "./ui";

export function CompanionDevices({ status, onChange }: { status: CompanionStatus | null; onChange?: () => void }) {
  const t = useT();
  if (!status) return null;
  return (
    <div className="space-y-2">
      <h4 className="text-xs font-semibold uppercase tracking-wide text-slate-500">{t("pair.connected")}</h4>
      {status.devices.length === 0 && <p className="text-sm text-slate-500">{t("pair.no_devices")}</p>}
      <ul className="space-y-1">
        {status.devices.map((d) => (
          <li key={d.conn_id} className="flex flex-wrap items-center gap-2 rounded-lg bg-emerald-50 px-2 py-1.5 text-sm dark:bg-emerald-950/40">
            <span className="inline-flex items-center gap-1 font-medium">
              <Smartphone className="h-4 w-4" /> {d.name}
            </span>
            <Badge>{t(`pair.mode.${d.mode}` as MessageKey)}</Badge>
            {d.active_mic && (
              <Badge tone="red">
                <Mic className="h-3 w-3" /> {t("pair.active_mic")}
              </Badge>
            )}
            {d.rtt_ms !== null && <span className="text-xs text-slate-500">{t("pair.rtt", { ms: Math.round(d.rtt_ms / 2) })}</span>}
            {d.battery?.level !== undefined && <span className="inline-flex items-center gap-0.5 text-xs text-slate-500">
                <Battery className="h-3.5 w-3.5" />
                {Math.round(d.battery.level * 100)}%
              </span>}
          </li>
        ))}
      </ul>
      {status.paired_devices.length > 0 && (
        <details className="text-sm">
          <summary className="cursor-pointer text-xs font-semibold uppercase tracking-wide text-slate-500">
            {t("pair.paired_devices")} ({status.paired_devices.length})
          </summary>
          <ul className="mt-1 space-y-1">
            {status.paired_devices.map((d) => (
              <li key={d.id} className="flex items-center gap-2">
                <span className="min-w-0 flex-1 truncate">
                  {d.name} {d.online && <span className="inline-flex items-center gap-1 text-xs text-emerald-600">
                      <span className="h-1.5 w-1.5 rounded-full bg-emerald-500" /> {t("pair.online")}
                    </span>}
                </span>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={async () => {
                    await api.revokeDevice(d.id);
                    onChange?.();
                  }}
                >
                  {t("pair.revoke")}
                </Button>
              </li>
            ))}
          </ul>
        </details>
      )}
    </div>
  );
}

export function useCompanionStatus(active: boolean, intervalMs = 1500) {
  const [status, setStatus] = useState<CompanionStatus | null>(null);
  const refresh = useCallback(() => {
    api.companionStatus().then(setStatus).catch(() => undefined);
  }, []);
  useEffect(() => {
    if (!active) return;
    refresh();
    const timer = window.setInterval(refresh, intervalMs);
    return () => window.clearInterval(timer);
  }, [active, intervalMs, refresh]);
  return { status, refresh };
}

export function PairPhoneModal({
  open,
  onClose,
  sessionId,
}: {
  open: boolean;
  onClose: () => void;
  sessionId?: number | null;
}) {
  const t = useT();
  const [info, setInfo] = useState<PairInfo | null>(null);
  const [ip, setIp] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [now, setNow] = useState(() => Date.now());
  const [openedAt, setOpenedAt] = useState(0);
  const { status, refresh } = useCompanionStatus(open);
  const clockOffset = useRef(0);
  const deviceCountAtStart = useRef<number | null>(null);

  const create = useCallback(
    async (chosenIp?: string | null) => {
      setLoading(true);
      setError(null);
      try {
        const res = await api.companionPair({ ip: chosenIp ?? undefined, session_id: sessionId ?? undefined });
        clockOffset.current = res.server_time * 1000 - Date.now();
        setInfo(res);
        setIp(res.ip);
        setOpenedAt(Date.now());
      } catch (e) {
        setError(errorMessage(e));
      } finally {
        setLoading(false);
      }
    },
    [sessionId],
  );

  useEffect(() => {
    if (open) {
      deviceCountAtStart.current = null;
      void create();
    } else {
      setInfo(null);
    }
  }, [open, create]);

  useEffect(() => {
    if (!open) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [open]);

  useEffect(() => {
    if (status && deviceCountAtStart.current === null) deviceCountAtStart.current = status.devices.length;
  }, [status]);

  const remaining = info ? Math.max(0, Math.round(info.expires_at - (now + clockOffset.current) / 1000)) : 0;
  const connectedNew = status !== null && deviceCountAtStart.current !== null && status.devices.length > deviceCountAtStart.current;
  const showHotspotHint = info !== null && !connectedNew && now - openedAt > 10_000;

  return (
    <Modal
      open={open}
      wide
      title={
        <>
          <Smartphone className="h-5 w-5 text-brand-600" /> {t("pair.title")}
        </>
      }
      onClose={onClose}
      footer={
        <>
          {status?.running && (
            <Button
              variant="ghost"
              onClick={async () => {
                await api.companionStop();
                refresh();
                onClose();
              }}
            >
              {t("pair.stop_server")}
            </Button>
          )}
          <Button variant="primary" onClick={onClose}>
            {t("common.close")}
          </Button>
        </>
      }
    >
      <div className="grid gap-5 sm:grid-cols-[minmax(0,15rem)_1fr]">
        <div className="space-y-2 text-center">
          {loading && !info && (
            <div className="flex h-60 items-center justify-center">
              <Spinner className="h-6 w-6" />
            </div>
          )}
          {info && (
            <>
              <div className={`rounded-xl bg-white p-2 ${remaining === 0 ? "opacity-20" : ""}`}>
                <img
                  alt="QR"
                  className="mx-auto w-full max-w-[15rem]"
                  src={`data:image/svg+xml;utf8,${encodeURIComponent(info.qr_svg)}`}
                />
              </div>
              {remaining > 0 ? (
                <p className="text-xs text-slate-500">{t("pair.expires", { s: remaining })}</p>
              ) : (
                <p className="text-xs font-medium text-red-600">{t("pair.expired")}</p>
              )}
              <Button size="sm" onClick={() => void create(ip)} loading={loading}>
                <RotateCw className="h-3.5 w-3.5" /> {t("pair.new_code")}
              </Button>
              <p className="break-all font-mono text-[0.7rem] text-slate-400">{info.url.replace(/\?t=.*/, "")}</p>
            </>
          )}
        </div>
        <div className="space-y-3 text-sm">
          <p>{t("pair.scan")}</p>
          {info && info.addresses.length > 1 && (
            <label className="block">
              <span className="mb-1 block text-xs font-semibold uppercase tracking-wide text-slate-500">{t("pair.network")}</span>
              <select className={inputCls} value={ip ?? ""} onChange={(e) => void create(e.target.value)}>
                {info.addresses.map((a) => (
                  <option key={a.ip} value={a.ip}>
                    {a.ip} {a.interface ? `— ${a.interface}` : ""} {a.hotspot ? `(${t("pair.hotspot")})` : ""}
                  </option>
                ))}
              </select>
            </label>
          )}
          {error && (
            <p className="flex items-start gap-1.5 rounded-lg bg-red-50 p-2 text-red-800 dark:bg-red-950/50 dark:text-red-200">
              <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" /> {error}
            </p>
          )}
          <p className="flex items-start gap-1.5 rounded-lg bg-slate-50 p-2 text-xs text-slate-600 dark:bg-slate-800 dark:text-slate-300">
            <Lock className="mt-px h-3.5 w-3.5 shrink-0" /> {t("pair.cert_hint")}
          </p>
          {showHotspotHint && (
            <p className="space-y-1 rounded-lg bg-amber-50 p-2 text-xs text-amber-900 dark:bg-amber-950/40 dark:text-amber-200">
              <span className="flex items-start gap-1.5">
                <Wifi className="mt-px h-3.5 w-3.5 shrink-0" /> {t("pair.not_connecting")}
              </span>
              <span className="flex items-start gap-1.5">
                <ShieldCheck className="mt-px h-3.5 w-3.5 shrink-0" /> {t("pair.firewall")}
              </span>
            </p>
          )}
          {info && (
            <p className="text-xs text-slate-500">
              {t("pair.ca_hint")} <span className="break-all font-mono">{info.ca_url}</span>
            </p>
          )}
          <CompanionDevices status={status} onChange={refresh} />
          {info && (
            <a className="inline-flex items-center gap-1 text-xs text-brand-600 hover:underline" href={info.local_url} target="_blank" rel="noreferrer">
              {t("pair.open_local")} <ExternalLink className="h-3 w-3" />
            </a>
          )}
        </div>
      </div>
    </Modal>
  );
}

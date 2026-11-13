import { CSSProperties, useEffect, useRef, useState } from "react";

/**
 * Official Tybee mascot cat and interactive Grinya components,
 * matching tybee.ru and examples/.
 */

export function TybeeCat({
  size = 32,
  className = "",
  style,
  animated = false,
}: {
  size?: number | string;
  className?: string;
  style?: CSSProperties;
  animated?: boolean;
}) {
  return (
    <img
      src="/cat.png"
      alt="Кот Тайби"
      width={size}
      height={size}
      className={`tybee-cat-mascot ${animated ? "cat-bounce" : ""} ${className}`.trim()}
      style={{
        width: size,
        height: size,
        objectFit: "contain",
        display: "inline-block",
        verticalAlign: "middle",
        ...style,
      }}
    />
  );
}

/** Static vector Grinya pill cat icon from examples/Grinya.svg */
export function GrinyaCat({
  width = 36,
  height = 21,
  className = "",
  style,
}: {
  width?: number | string;
  height?: number | string;
  className?: string;
  style?: CSSProperties;
}) {
  return (
    <svg
      viewBox="0 0 661.22 391.12"
      width={width}
      height={height}
      className={`grinya-pill-cat ${className}`.trim()}
      style={{ display: "inline-block", verticalAlign: "middle", ...style }}
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
    >
      <path
        fill="#156bff"
        d="M195.76,0h270.1C573.68,0,661.22,87.54,661.22,195.36v.39c0,107.82-87.54,195.36-195.36,195.36H195.36C87.54,391.12,0,303.58,0,195.76H0C0,87.72,87.72,0,195.76,0Z"
      />
      <g stroke="#ffffff" fill="#ffffff" strokeMiterlimit="10">
        <path
          strokeWidth="10"
          d="M161.29,187.92c-10.02,14.29-18.04,33.84-18.04,60.9s8.27,45.62,18.04,60.16h-12.78c-9.02-12.03-19.3-31.33-19.3-60.16s10.28-48.87,19.3-60.9h12.78Z"
        />
        <path
          strokeWidth="3"
          d="M200.9,82.14h19.55l45.37,77.7h-24.56l-30.58-53.14-30.58,53.14h-24.56l45.37-77.7Z"
        />
        <path
          strokeWidth="10"
          d="M287.38,260.36c-4.51,0-8.36-1.59-11.53-4.76-3.18-3.17-4.76-7.02-4.76-11.53s1.58-8.35,4.76-11.53c3.17-3.17,7.02-4.76,11.53-4.76s8.56,1.59,11.66,4.76c3.09,3.18,4.64,7.02,4.64,11.53s-1.55,8.35-4.64,11.53c-3.1,3.17-6.98,4.76-11.66,4.76Z"
        />
        <path
          strokeWidth="10"
          d="M373.35,260.36c-4.51,0-8.35-1.59-11.53-4.76-3.17-3.17-4.76-7.02-4.76-11.53s1.59-8.35,4.76-11.53c3.17-3.17,7.02-4.76,11.53-4.76s8.56,1.59,11.66,4.76c3.09,3.18,4.64,7.02,4.64,11.53s-1.55,8.35-4.64,11.53c-3.1,3.17-6.98,4.76-11.66,4.76Z"
        />
        <path
          strokeWidth="3"
          d="M440.53,82.14h19.55l45.37,77.7h-24.56l-30.59-53.14-30.58,53.14h-24.56l45.37-77.7Z"
        />
        <path
          strokeWidth="10"
          d="M499.93,308.98c9.77-14.54,18.04-33.84,18.04-60.66s-8.27-45.86-18.04-60.4h12.78c9.02,12.03,19.3,31.33,19.3,60.4s-10.28,48.38-19.3,60.66h-12.78Z"
        />
      </g>
    </svg>
  );
}

/**
 * Interactive Grinya cat from tybee.ru:
 * tracks cursor, blinks, breathes, sleeps when idle, plays on approach, purrs on pet, jumps on click.
 */
export function InteractiveGrinya({
  width = 110,
  className = "",
  showHints = true,
}: {
  width?: number;
  className?: string;
  showHints?: boolean;
}) {
  const boxRef = useRef<HTMLDivElement>(null);
  const [sleep, setSleep] = useState(false);
  const [blink, setBlink] = useState(false);
  const [play, setPlay] = useState(false);
  const [pet, setPet] = useState(false);
  const [clickAnim, setClickAnim] = useState(false);
  const [eyeOffset, setEyeOffset] = useState({ x: 0, y: 0 });
  const overRef = useRef(false);

  useEffect(() => {
    let idleTimer: number;
    let playTimer: number;
    let petTimer: number;

    const wake = () => {
      setSleep(false);
      window.clearTimeout(idleTimer);
      idleTimer = window.setTimeout(() => setSleep(true), 9000);
    };
    wake();

    const blinkInterval = window.setInterval(() => {
      setBlink(true);
      window.setTimeout(() => setBlink(false), 140);
    }, 3600);

    const onMouseMove = (e: MouseEvent) => {
      wake();
      const box = boxRef.current;
      if (!box) return;
      const r = box.getBoundingClientRect();
      const cx = r.left + r.width / 2;
      const cy = r.top + r.height / 2;
      const dx = Math.max(-14, Math.min(14, (e.clientX - cx) / 10));
      const dy = Math.max(-8, Math.min(8, (e.clientY - cy) / 10));
      setEyeOffset({ x: dx, y: dy });

      const dist = Math.hypot(e.clientX - cx, e.clientY - cy);
      if (dist < 160 && !overRef.current) {
        setPlay(true);
        window.clearTimeout(playTimer);
        playTimer = window.setTimeout(() => setPlay(false), 400);
      }
      if (overRef.current) {
        setPet(true);
        window.clearTimeout(petTimer);
        petTimer = window.setTimeout(() => setPet(false), 600);
      }
    };

    window.addEventListener("mousemove", onMouseMove, { passive: true });
    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.clearTimeout(idleTimer);
      window.clearTimeout(playTimer);
      window.clearTimeout(petTimer);
      window.clearInterval(blinkInterval);
    };
  }, []);

  const handleClick = () => {
    setSleep(false);
    setClickAnim(false);
    requestAnimationFrame(() => setClickAnim(true));
    window.setTimeout(() => setClickAnim(false), 800);
  };

  const cls = [
    "ys-grinya",
    sleep && !pet ? "is-sleep" : "",
    blink && !sleep ? "is-blink" : "",
    play && !sleep ? "is-play" : "",
    pet ? "is-pet" : "",
    clickAnim ? "is-click" : "",
    className,
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div
      ref={boxRef}
      className={cls}
      style={{ width }}
      onMouseEnter={() => {
        overRef.current = true;
        setPlay(false);
        setSleep(false);
      }}
      onMouseLeave={() => {
        overRef.current = false;
        setPet(false);
      }}
      onClick={handleClick}
      role="button"
      tabIndex={0}
      aria-label="Гриня — интерактивный кот Тайби"
      onKeyDown={(e) => {
        if (e.key === "Enter" || e.key === " ") {
          e.preventDefault();
          handleClick();
        }
      }}
    >
      <svg viewBox="0 0 661.22 391.12" fill="none" xmlns="http://www.w3.org/2000/svg">
        <g className="ys-grinya__body">
          <path
            fill="#156bff"
            d="M195.76,0h270.1C573.68,0,661.22,87.54,661.22,195.36v.39c0,107.82-87.54,195.36-195.36,195.36H195.36C87.54,391.12,0,303.58,0,195.76H0C0,87.72,87.72,0,195.76,0Z"
          />
          <g fill="#fff" stroke="#fff" strokeMiterlimit="10">
            <path
              strokeWidth="10"
              d="M161.29,187.92c-10.02,14.29-18.04,33.84-18.04,60.9s8.27,45.62,18.04,60.16h-12.78c-9.02-12.03-19.3-31.33-19.3-60.16s10.28-48.87,19.3-60.9h12.78Z"
            />
            <path
              className="ys-grinya__ear"
              strokeWidth="3"
              d="M200.9,82.14h19.55l45.37,77.7h-24.56l-30.58-53.14-30.58,53.14h-24.56l45.37-77.7Z"
            />
            <g style={{ transform: sleep || blink ? undefined : `translate(${eyeOffset.x}px, ${eyeOffset.y}px)`, transition: "transform .12s" }}>
              <g className="ys-grinya__eye">
                <path
                  strokeWidth="10"
                  d="M287.38,260.36c-4.51,0-8.36-1.59-11.53-4.76-3.18-3.17-4.76-7.02-4.76-11.53s1.58-8.35,4.76-11.53c3.17-3.17,7.02-4.76,11.53-4.76s8.56,1.59,11.66,4.76c3.09,3.18,4.64,7.02,4.64,11.53s-1.55,8.35-4.64,11.53c-3.1,3.17-6.98,4.76-11.66,4.76Z"
                />
              </g>
            </g>
            <g style={{ transform: sleep || blink ? undefined : `translate(${eyeOffset.x}px, ${eyeOffset.y}px)`, transition: "transform .12s" }}>
              <g className="ys-grinya__eye">
                <path
                  strokeWidth="10"
                  d="M373.35,260.36c-4.51,0-8.35-1.59-11.53-4.76-3.17-3.17-4.76-7.02-4.76-11.53s1.59-8.35,4.76-11.53c3.17-3.17,7.02-4.76,11.53-4.76s8.56,1.59,11.66,4.76c3.09,3.18,4.64,7.02,4.64,11.53s-1.55,8.35-4.64,11.53c-3.1,3.17-6.98,4.76-11.66,4.76Z"
                />
              </g>
            </g>
            <path
              className="ys-grinya__ear"
              strokeWidth="3"
              d="M440.53,82.14h19.55l45.37,77.7h-24.56l-30.59-53.14-30.58,53.14h-24.56l45.37-77.7Z"
            />
            <path
              strokeWidth="10"
              d="M499.93,308.98c9.77-14.54,18.04-33.84,18.04-60.66s-8.27-45.86-18.04-60.4h12.78c9.02,12.03,19.3,31.33,19.3,60.4s-10.28,48.38-19.3,60.66h-12.78Z"
            />
          </g>
        </g>
      </svg>
      {showHints && (
        <>
          <div className="ys-grinya__zzz">z z z</div>
          <div className="ys-grinya__purr">мурр~</div>
          <div className="ys-grinya__heart">💙</div>
        </>
      )}
    </div>
  );
}

/**
 * Empty state card with interactive Grinya cat, matching tybee.ru's ys-empty block.
 */
export function TybeeEmptyState({
  title = "Пока ничего нет",
  description,
  action,
}: {
  title?: string;
  description?: string;
  action?: React.ReactNode;
}) {
  return (
    <div className="ys-empty-card">
      <InteractiveGrinya width={110} />
      <div className="ys-empty-title">{title}</div>
      {description && <div className="ys-empty-desc muted">{description}</div>}
      {action && <div className="ys-empty-action">{action}</div>}
    </div>
  );
}

export function TybeeBrand({
  tag = "панель",
  className = "",
}: {
  tag?: string;
  className?: string;
}) {
  return (
    <div className={`brand-identity ${className}`.trim()}>
      <img src="/logobig.png" alt="tybee" className="brand-logo-img" />
      {tag && <span className="brand-tag">{tag}</span>}
    </div>
  );
}

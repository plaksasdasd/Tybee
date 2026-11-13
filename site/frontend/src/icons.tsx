// Minimal stroke icons (24x24 grid) for the navigation and a few buttons.
const P: Record<string, string> = {
  me: "M12 12a4 4 0 1 0 0-8 4 4 0 0 0 0 8zM4.5 20c.8-3.4 3.8-5.5 7.5-5.5s6.7 2.1 7.5 5.5",
  support: "M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v9a1.5 1.5 0 0 1-1.5 1.5H9l-5 4zM8.5 9h7M8.5 12h4",
  finance: "M3.5 7.5A1.5 1.5 0 0 1 5 6h14a1.5 1.5 0 0 1 1.5 1.5v10A1.5 1.5 0 0 1 19 19H5a1.5 1.5 0 0 1-1.5-1.5zM3.5 10.5h17M15.5 14.5h2",
  covers: "M4 5.5A1.5 1.5 0 0 1 5.5 4h13A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5h-13A1.5 1.5 0 0 1 4 18.5zM9 11a2 2 0 1 0 0-4 2 2 0 0 0 0 4zM20 15l-4.5-4.5L6 20",
  catalog: "M12 3.5l8 4v9l-8 4-8-4v-9zM4 7.5l8 4 8-4M12 11.5v9",
  broadcasts: "M4 10v4h3l6 4.5v-13L7 10zM16.5 9a4 4 0 0 1 0 6M19 6.5a7.5 7.5 0 0 1 0 11",
  avito: "M4.5 9.5l1.2-5h12.6l1.2 5M5 9.5V20h14V9.5M4.5 9.5h15M10 20v-5h4v5",
  staff: "M9 11a3.5 3.5 0 1 0 0-7 3.5 3.5 0 0 0 0 7zM2.5 20c.6-3.2 3.2-5 6.5-5s5.9 1.8 6.5 5M15.5 4.3a3.5 3.5 0 0 1 0 6.4M18 15.2c1.9.6 3.1 2.2 3.5 4.8",
  audit: "M9 4h6v3H9zM7 5.5H5.5V20h13V5.5H17M8.5 11.5h7M8.5 15h4.5",
  leaders: "M8 4h8v3.6a4 4 0 0 1-8 0zM8 5.5H5.5v2a3 3 0 0 0 2.6 3M16 5.5h2.5v2a3 3 0 0 1-2.6 3M12 12v3.5M8.5 20h7M9.8 15.5h4.4l.6 4.5h-5.6z",
  logout: "M14 4h4.5A1.5 1.5 0 0 1 20 5.5v13a1.5 1.5 0 0 1-1.5 1.5H14M10 8l-4 4 4 4M6 12h10",
  clip: "M20 11.5l-7.8 7.8a5 5 0 0 1-7.1-7.1l8.1-8.1a3.3 3.3 0 0 1 4.7 4.7l-8.1 8.1a1.7 1.7 0 0 1-2.4-2.4l7.4-7.4",
  guide: "M12 3.5a8.5 8.5 0 1 0 0 17 8.5 8.5 0 0 0 0-17zM15.5 8.5l-2 5-5 2 2-5z",
};

export function Icon({ name }: { name: keyof typeof P | string }) {
  return (
    <svg viewBox="0 0 24 24" width="18" height="18" fill="none" stroke="currentColor" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <path d={P[name] ?? ""} />
    </svg>
  );
}

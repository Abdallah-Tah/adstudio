"use client";

import { createContext, useContext, useEffect, useState } from "react";

export type Crumb = { label: string; href?: string };
export type HeaderBadge = { tone: string; label: string; dot?: boolean };
export type HeaderMetric = { label: string; value: string; accent?: string };

export type HeaderData = {
  breadcrumb: Crumb[];
  badges?: HeaderBadge[];
  metrics?: HeaderMetric[];
  progress?: number | null; // 0..100
};

const EMPTY: HeaderData = { breadcrumb: [] };

type Store = { data: HeaderData; set: (d: HeaderData) => void };
const HeaderContext = createContext<Store>({ data: EMPTY, set: () => {} });

export function HeaderProvider({ children }: { children: React.ReactNode }) {
  const [data, setData] = useState<HeaderData>(EMPTY);
  return (
    <HeaderContext.Provider value={{ data, set: setData }}>
      {children}
    </HeaderContext.Provider>
  );
}

export function useHeaderData() {
  return useContext(HeaderContext).data;
}

// Pages call this in render to publish their header context. Serialised deps
// keep it from looping on every render.
export function usePageHeader(data: HeaderData) {
  const { set } = useContext(HeaderContext);
  const key = JSON.stringify(data);
  useEffect(() => {
    set(data);
    return () => set(EMPTY);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);
}

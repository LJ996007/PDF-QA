/** PDF高亮控制Hook */
import { useState, useCallback } from 'react';
import type { Highlight } from '../types';

export function usePdfHighlight() {
  const [highlights, setHighlights] = useState<Highlight[]>([]);

  const addHighlight = useCallback((highlight: Highlight) => {
    setHighlights((prev) => {
      // 移除同页的高亮，只保留新的
      const filtered = prev.filter((h) => h.page !== highlight.page);
      return [...filtered, highlight];
    });
  }, []);

  const removeHighlight = useCallback((page: number) => {
    setHighlights((prev) => prev.filter((h) => h.page !== page));
  }, []);

  const clearHighlights = useCallback(() => {
    setHighlights([]);
  }, []);

  const getHighlightForPage = useCallback((page: number) => {
    return highlights.find((h) => h.page === page);
  }, [highlights]);

  return {
    highlights,
    addHighlight,
    removeHighlight,
    clearHighlights,
    getHighlightForPage,
  };
}

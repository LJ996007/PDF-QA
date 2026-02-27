import { chromium } from 'playwright';
import path from 'node:path';

const baseUrl = 'http://127.0.0.1:5173';
const pdfPath = path.resolve(process.cwd(), 'tmp_e2e/smoke.pdf');

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

await page.goto(baseUrl, { waitUntil: 'networkidle', timeout: 45000 });
await page.locator('input[type="file"]').setInputFiles(pdfPath);

const modal = page.locator('.upload-choice-modal');
await modal.waitFor({ state: 'visible', timeout: 15000 });
await modal.getByRole('button', { name: /否，仅加载|否,仅加载|否/ }).click();

await page.locator('.pdf-viewer').waitFor({ state: 'visible', timeout: 60000 });
await page.locator('.page-wrapper[data-page-number="1"]').first().waitFor({ state: 'visible', timeout: 60000 });

await page.evaluate(async () => {
  const { useDocumentStore } = await import('/src/stores/documentStore.ts');
  useDocumentStore.setState({
    viewMode: 'grid',
    messages: [
      {
        id: `assistant_test_${Date.now()}`,
        role: 'assistant',
        content: '测试跳转 [ref-1]',
        references: [
          {
            id: 'chunk_test_1',
            refId: 'ref-1',
            content: 'smoke reference',
            page: 1,
            bbox: { page: 1, x: 72, y: 120, w: 180, h: 22 },
            source: 'native',
          },
        ],
        activeRefs: [],
        timestamp: new Date(),
        isStreaming: false,
      },
    ],
    highlights: [],
    currentPage: 1,
    isLoading: false,
  });

  const perf = { calls: [] };
  const proto = HTMLElement.prototype;
  const original = proto.scrollTo;
  if (!window.__scrollPatched) {
    proto.scrollTo = function (...args) {
      let top = null;
      let behavior = null;
      if (args[0] && typeof args[0] === 'object') {
        top = typeof args[0].top === 'number' ? args[0].top : null;
        behavior = args[0].behavior || null;
      }
      perf.calls.push({
        top,
        behavior,
        t: performance.now(),
        className: this.className || '',
      });
      return original.apply(this, args);
    };
    window.__scrollPatched = true;
  }
  window.__jumpPerf = perf;
});

await page.locator('.pdf-grid-container').first().waitFor({ state: 'visible', timeout: 8000 });

const refLink = page.locator('.markdown-ref').first();
await refLink.waitFor({ state: 'visible', timeout: 10000 });

const clickStart = Date.now();
await refLink.click();
await page.locator('.highlight-rect').first().waitFor({ state: 'visible', timeout: 12000 });
const highlightLatencyMs = Date.now() - clickStart;

await page.waitForTimeout(1200);

const result = await page.evaluate(async () => {
  const { useDocumentStore } = await import('/src/stores/documentStore.ts');
  const state = useDocumentStore.getState();
  const perf = window.__jumpPerf || { calls: [] };
  const filtered = perf.calls.filter((c) => c.top !== null);
  return {
    currentPage: state.currentPage,
    currentViewMode: state.viewMode,
    highlightsCount: state.highlights.length,
    scrollCalls: filtered.length,
    scrollCallsDetail: filtered,
  };
});

console.log(JSON.stringify({ highlightLatencyMs, ...result }, null, 2));
await browser.close();

import { chromium } from 'playwright';
import path from 'node:path';

const baseUrl = 'http://127.0.0.1:5173';
const pdfPath = path.resolve(process.cwd(), 'tmp_e2e/smoke.pdf');

function pickModalButton(modal) {
  return modal.getByRole('button', { name: /否，仅加载|否,仅加载|否/ });
}

const browser = await chromium.launch({ headless: true });
const page = await browser.newPage({ viewport: { width: 1600, height: 1000 } });

page.on('pageerror', (error) => {
  console.error('[pageerror]', error.message);
});

await page.goto(baseUrl, { waitUntil: 'networkidle', timeout: 45000 });

const fileInput = page.locator('input[type="file"]');
await fileInput.setInputFiles(pdfPath);

const modal = page.locator('.upload-choice-modal');
await modal.waitFor({ state: 'visible', timeout: 15000 });
await pickModalButton(modal).click();

await page.locator('.pdf-viewer').waitFor({ state: 'visible', timeout: 60000 });
await page.locator('.page-wrapper[data-page-number="1"]').first().waitFor({ state: 'visible', timeout: 60000 });

await page.evaluate(async () => {
  const { useDocumentStore } = await import('/src/stores/documentStore.ts');

  useDocumentStore.setState({
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
  const pageEl = document.querySelector('.page-wrapper');
  let scroller = null;
  let probe = pageEl;
  while (probe && probe !== document.body) {
    const style = window.getComputedStyle(probe);
    if (/(auto|scroll)/.test(style.overflowY) || /(auto|scroll)/.test(style.overflow)) {
      scroller = probe;
      break;
    }
    probe = probe.parentElement;
  }

  if (scroller && typeof scroller.scrollTo === 'function') {
    const original = scroller.scrollTo.bind(scroller);
    scroller.scrollTo = (...args) => {
      let top = null;
      let behavior = null;
      if (args[0] && typeof args[0] === 'object') {
        top = typeof args[0].top === 'number' ? args[0].top : null;
        behavior = args[0].behavior || null;
      }
      perf.calls.push({ top, behavior, t: performance.now() });
      return original(...args);
    };
  }

  window.__jumpPerf = perf;
});

const refLink = page.locator('.markdown-ref').first();
await refLink.waitFor({ state: 'visible', timeout: 10000 });

const clickStart = Date.now();
await refLink.click();
await page.locator('.highlight-rect').first().waitFor({ state: 'visible', timeout: 8000 });
const highlightLatencyMs = Date.now() - clickStart;

await page.waitForTimeout(900);

const result = await page.evaluate(async () => {
  const { useDocumentStore } = await import('/src/stores/documentStore.ts');
  const state = useDocumentStore.getState();
  const perf = window.__jumpPerf || { calls: [] };
  return {
    currentPage: state.currentPage,
    highlightsCount: state.highlights.length,
    scrollCalls: perf.calls.length,
    scrollCallsDetail: perf.calls,
  };
});

console.log(JSON.stringify({ highlightLatencyMs, ...result }, null, 2));

await browser.close();

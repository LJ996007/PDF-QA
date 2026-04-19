export const normalizePageNumbers = (pages: Iterable<number>): number[] => {
    const uniquePages = new Set<number>();

    for (const page of pages) {
        const pageNumber = Number(page);
        if (Number.isInteger(pageNumber) && pageNumber > 0) {
            uniquePages.add(pageNumber);
        }
    }

    return Array.from(uniquePages).sort((a, b) => a - b);
};

export const parsePageSelectionInput = (raw: string, maxPage?: number): number[] => {
    const tokens = raw
        .split(/[,\s]+/)
        .map((item) => item.trim())
        .filter(Boolean);

    const pages = new Set<number>();

    for (const token of tokens) {
        const rangeMatch = token.match(/^(\d+)-(\d+)$/);
        if (rangeMatch) {
            const start = Number(rangeMatch[1]);
            const end = Number(rangeMatch[2]);
            if (!Number.isInteger(start) || !Number.isInteger(end) || start <= 0 || end < start) {
                continue;
            }

            const boundedStart = Math.max(1, start);
            const boundedEnd = typeof maxPage === 'number' ? Math.min(maxPage, end) : end;
            for (let page = boundedStart; page <= boundedEnd; page += 1) {
                pages.add(page);
            }
            continue;
        }

        const page = Number(token);
        if (!Number.isInteger(page) || page <= 0) {
            continue;
        }
        if (typeof maxPage === 'number' && page > maxPage) {
            continue;
        }
        pages.add(page);
    }

    return normalizePageNumbers(pages);
};

const toPageRanges = (pages: number[]): Array<{ start: number; end: number }> => {
    const normalizedPages = normalizePageNumbers(pages);
    if (normalizedPages.length === 0) {
        return [];
    }

    const ranges: Array<{ start: number; end: number }> = [];
    let start = normalizedPages[0];
    let end = normalizedPages[0];

    for (let index = 1; index < normalizedPages.length; index += 1) {
        const page = normalizedPages[index];
        if (page === end + 1) {
            end = page;
            continue;
        }

        ranges.push({ start, end });
        start = page;
        end = page;
    }

    ranges.push({ start, end });
    return ranges;
};

export const formatPageSelectionRanges = (pages: number[], separator = '、'): string => {
    return toPageRanges(pages)
        .map(({ start, end }) => (start === end ? String(start) : `${start}-${end}`))
        .join(separator);
};

export const formatPageSelectionSummary = (
    pages: number[],
    options?: {
        emptyText?: string;
        separator?: string;
    }
): string => {
    const normalizedPages = normalizePageNumbers(pages);
    if (normalizedPages.length === 0) {
        return options?.emptyText ?? '未选择';
    }

    return formatPageSelectionRanges(normalizedPages, options?.separator ?? '、');
};

export const formatPageSelectionLabel = (pages: number[]): string => {
    const summary = formatPageSelectionSummary(pages, { emptyText: '' });
    return summary ? `第${summary}页` : '';
};

const REF_TAG_PATTERN = /\[ref-(\d+)\]/g;

type MarkdownNode = {
    type: string;
    value?: string;
    url?: string;
    title?: string | null;
    children?: MarkdownNode[];
};

function createTextNode(value: string): MarkdownNode {
    return { type: 'text', value };
}

function createRefNode(refNumber: string): MarkdownNode {
    return {
        type: 'link',
        url: `#ref-${refNumber}`,
        title: `ref-${refNumber}`,
        children: [createTextNode(`[ref-${refNumber}]`)],
    };
}

function splitTextNode(value: string): MarkdownNode[] {
    const result: MarkdownNode[] = [];
    let lastIndex = 0;
    REF_TAG_PATTERN.lastIndex = 0;
    let match = REF_TAG_PATTERN.exec(value);

    while (match) {
        const [fullMatch, refNumber] = match;
        const matchIndex = match.index;

        if (matchIndex > lastIndex) {
            result.push(createTextNode(value.slice(lastIndex, matchIndex)));
        }

        result.push(createRefNode(refNumber));
        lastIndex = matchIndex + fullMatch.length;
        match = REF_TAG_PATTERN.exec(value);
    }

    if (lastIndex < value.length) {
        result.push(createTextNode(value.slice(lastIndex)));
    }

    return result.length > 0 ? result : [createTextNode(value)];
}

function transformNodes(nodes: MarkdownNode[]): MarkdownNode[] {
    const transformed: MarkdownNode[] = [];

    nodes.forEach((node) => {
        if (node.type === 'text' && typeof node.value === 'string') {
            transformed.push(...splitTextNode(node.value));
            return;
        }

        if (
            node.type === 'inlineCode'
            || node.type === 'code'
            || node.type === 'link'
            || node.type === 'linkReference'
        ) {
            transformed.push(node);
            return;
        }

        if (Array.isArray(node.children) && node.children.length > 0) {
            transformed.push({
                ...node,
                children: transformNodes(node.children),
            });
            return;
        }

        transformed.push(node);
    });

    return transformed;
}

export function remarkRefTag() {
    return (tree: MarkdownNode): void => {
        if (!Array.isArray(tree.children) || tree.children.length === 0) {
            return;
        }

        tree.children = transformNodes(tree.children);
    };
}


import React from 'react';
import ReactMarkdown, { type Components } from 'react-markdown';
import remarkGfm from 'remark-gfm';
import { remarkRefTag } from '../../utils/remarkRefTag';

interface ChatMarkdownContentProps {
    content: string;
    validRefIds: ReadonlySet<string>;
    onRefClick: (refId: string, isValid: boolean) => void;
}

const REF_URL_PATTERN = /^\/__ref__\/(\d+)\/?$/;

export const ChatMarkdownContent: React.FC<ChatMarkdownContentProps> = ({
    content,
    validRefIds,
    onRefClick,
}) => {
    const components = React.useMemo<Components>(() => ({
        a: ({ href, children }) => {
            const match = href ? REF_URL_PATTERN.exec(href) : null;

            if (match) {
                const refNumber = match[1];
                const refId = `ref-${refNumber}`;
                const isValid = validRefIds.has(refId);

                return (
                    <button
                        type="button"
                        className={`ref-tag inline-ref markdown-ref${isValid ? '' : ' invalid'}`}
                        onClick={() => onRefClick(refId, isValid)}
                        title={isValid ? '点击跳转到引用位置' : '未找到对应引用，请先核对引用来源'}
                    >
                        {refNumber}
                    </button>
                );
            }

            return (
                <a href={href} target="_blank" rel="noreferrer noopener">
                    {children}
                </a>
            );
        },
    }), [onRefClick, validRefIds]);

    return (
        <ReactMarkdown remarkPlugins={[remarkGfm, remarkRefTag]} components={components}>
            {content}
        </ReactMarkdown>
    );
};

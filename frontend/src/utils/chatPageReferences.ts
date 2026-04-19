import type { ChatPageReferenceGroup } from '../stores/documentStore';
import { formatPageSelectionLabel } from './pageSelection';

export const buildPageReferenceGroupDefinitionLine = (group: ChatPageReferenceGroup): string => (
    `${group.placeholder}=${formatPageSelectionLabel(group.pages)}`
);

export const buildPageReferenceGroupAppendix = (groups: ChatPageReferenceGroup[]): string => {
    if (groups.length === 0) {
        return '';
    }

    const definitions = groups.map(buildPageReferenceGroupDefinitionLine).join('\n');
    return `页面引用组定义：\n${definitions}`;
};

export const appendPageReferenceGroupsToHistoryContent = (
    content: string,
    groups: ChatPageReferenceGroup[]
): string => {
    const appendix = buildPageReferenceGroupAppendix(groups);
    if (!appendix) {
        return content;
    }

    return `${content}\n\n${appendix}`;
};

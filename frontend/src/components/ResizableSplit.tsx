/** 可调节分隔栏组件 */
import React from 'react';
import {
  Panel,
  PanelGroup,
  PanelResizeHandle,
} from 'react-resizable-panels';

interface ResizableSplitProps {
  left: React.ReactNode;
  right: React.ReactNode;
  defaultLeftSize?: number;
  minLeftSize?: number;
  minRightSize?: number;
}

export function ResizableSplit({
  left,
  right,
  defaultLeftSize = 50,
  minLeftSize = 30,
  minRightSize = 30,
}: ResizableSplitProps) {
  return (
    <PanelGroup direction="horizontal" className="h-full">
      <Panel defaultSize={defaultLeftSize} minSize={minLeftSize}>
        {left}
      </Panel>
      <PanelResizeHandle className="w-2 bg-gray-200 hover:bg-blue-400 transition-colors cursor-col-resize" />
      <Panel minSize={minRightSize}>
        {right}
      </Panel>
    </PanelGroup>
  );
}

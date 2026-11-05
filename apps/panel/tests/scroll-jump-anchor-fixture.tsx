import { createRoot } from "react-dom/client";
import { useEffect, useRef, useState } from "react";
import { MessageList, type Anchor, type ListHandle } from "../src/react/pages/chat/message-list";

const keys = Array.from({ length: 12 }, (_, i) => `row-${i}`);
function Probe() {
  const [stick, setStick] = useState(true);
  const [hold, setHold] = useState<Anchor | null>(null);
  const [extra, setExtra] = useState(0);
  const list = useRef<ListHandle>(null);
  const userScrolls = useRef(0);
  useEffect(() => {
    Object.assign(window, { anchorProbe: {
      grow: () => setExtra((value) => value + 100),
      pin: () => { setHold(null); setStick(true); },
      hold: (offset: number, missing = false) => {
        const anchor = list.current?.capture();
        if (!anchor) throw new Error("No visible source anchor");
        setStick(false);
        setHold(missing ? { key: "missing", offset: 0 } : { ...anchor, offset: anchor.offset + offset });
      },
      read: () => {
        const el = document.querySelector<HTMLElement>(".scroll")!;
        return { top: el.scrollTop, gap: el.scrollHeight - el.clientHeight - el.scrollTop, height: el.scrollHeight, stick, userScrolls: userScrolls.current };
      },
    } });
  }, [stick]);
  return <><style>{`.scroll { height: 300px; overflow-y: auto; overflow-anchor: none; scroll-behavior: auto !important; }`}</style>
    <MessageList keys={keys} stick={stick} hold={hold} listRef={list}
      onStick={(value) => setStick(value)} onNearTop={() => {}}
      onUserScroll={() => { userScrolls.current += 1; setHold(null); }}>
      <div>{keys.map((key, index) => <div key={key} data-history-source={key} style={{ height: 100 + (index === keys.length - 1 ? extra : 0) }}>{key}</div>)}</div>
    </MessageList></>;
}
createRoot(document.getElementById("root")!).render(<Probe />);

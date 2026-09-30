/** Dấu tác giả: LezioneLive — created by Turin. Giữ dòng "Created by Turin" trên mọi tab trình duyệt. */

export const AUTHOR = "Turin";
export const CREATED_BY = `Created by ${AUTHOR}`;

function brandTitle(doc: Document): void {
  if (!doc.title.includes(AUTHOR)) doc.title = `${doc.title || "LezioneLive"} · ${CREATED_BY}`;
}

/** Gắn chữ ký vào tiêu đề tab (và giữ nguyên nếu trang đổi tiêu đề), in dấu tác giả ra console. */
export function applyBranding(doc: Document = document): void {
  brandTitle(doc);
  const title = doc.querySelector("title");
  if (title) new MutationObserver(() => brandTitle(doc)).observe(title, { childList: true, characterData: true, subtree: true });
  const meta = doc.createElement("meta");
  meta.name = "author";
  meta.content = AUTHOR;
  doc.head.appendChild(meta);
  if (doc === document) {
    console.info(`%cLezioneLive%c  ${CREATED_BY}`, "font-weight:700;color:#2563eb", "color:inherit");
  }
}

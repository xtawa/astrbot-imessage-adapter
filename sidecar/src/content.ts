import { randomUUID } from "node:crypto";
import { createWriteStream } from "node:fs";
import { readdir, rename, rm, stat } from "node:fs/promises";
import { join } from "node:path";
import { Readable, Transform } from "node:stream";
import { pipeline } from "node:stream/promises";
import type { Content, Message } from "@spectrum-ts/core";
import type { IMessageMessage } from "@spectrum-ts/imessage";

export interface WirePart {
  type: string;
  text?: string;
  path?: string;
  name?: string;
  mime_type?: string;
  target_id?: string;
  sender_id?: string;
  emoji?: string;
}

export async function saveMedia(
  content: Extract<Content, { type: "attachment" | "voice" }>,
  directory: string, maxBytes: number, cacheBytes: number,
): Promise<WirePart> {
  if (content.size !== undefined && content.size > maxBytes) throw new Error("attachment_too_large");
  const files = await readdir(directory);
  const used = (await Promise.all(files.map(async name => (await stat(join(directory, name))).size))).reduce((a, b) => a + b, 0);
  const capacity = Math.min(maxBytes, cacheBytes - used);
  if (capacity <= 0) throw new Error("media_cache_full");
  const extension = (content.name ?? "").match(/\.[A-Za-z0-9]{1,10}$/)?.[0] ?? ".bin";
  const destination = join(directory, randomUUID() + extension);
  const temporary = destination + ".part";
  let size = 0;
  try {
    const stream = await content.stream();
    const limiter = new Transform({
      transform(chunk: Buffer, _encoding, done) {
        size += chunk.byteLength;
        if (size > capacity) done(new Error("attachment_limit"));
        else done(null, chunk);
      },
    });
    await pipeline(Readable.fromWeb(stream as Parameters<typeof Readable.fromWeb>[0]), limiter, createWriteStream(temporary, { flags: "wx", mode: 0o600 }));
    await rename(temporary, destination);
    return { type: content.type, path: destination, name: content.name ?? `attachment${extension}`, mime_type: content.mimeType };
  } catch (error) {
    await rm(temporary, { force: true });
    throw error;
  }
}

export async function normalizeContent(
  content: Content, save: (value: Extract<Content, { type: "attachment" | "voice" }>) => Promise<WirePart>,
  depth = 0,
): Promise<WirePart[]> {
  if (depth > 8) return [{ type: "text", text: "[嵌套消息超出限制]" }];
  switch (content.type) {
    case "text": return [{ type: "text", text: content.text }];
    case "markdown": return [{ type: "text", text: content.markdown }];
    case "attachment":
    case "voice":
      try { return [await save(content)]; }
      catch { return [{ type: "text", text: `[附件无法下载或超出限制: ${content.name ?? "attachment"}]` }]; }
    case "group": {
      const parts: WirePart[] = [];
      for (const item of content.items) parts.push(...await normalizeContent(item.content, save, depth + 1));
      return parts;
    }
    case "reply": {
      const target = content.target;
      return [{ type: "reply", target_id: target.id, sender_id: target.sender?.id,
        text: target.content.type === "text" ? target.content.text : "" },
        ...await normalizeContent(content.content, save, depth + 1)];
    }
    case "reaction": return [{ type: "reaction", emoji: content.emoji, target_id: content.target.id }];
    case "richlink": return [{ type: "text", text: content.url }];
    case "app": {
      try {
        const layout = await content.layout();
        const url = await content.url();
        return [{ type: "text", text: [layout.caption, layout.subcaption, layout.summary, url].filter(Boolean).join("\n") }];
      } catch { return [{ type: "text", text: "[App 卡片内容不可用]" }]; }
    }
    case "contact": {
      const name = content.name?.formatted ?? [content.name?.first, content.name?.last].filter(Boolean).join(" ");
      return [{ type: "text", text: `[联系人] ${name}\n${[...(content.phones ?? []).map(x => x.value), ...(content.emails ?? []).map(x => x.value)].join("\n")}` }];
    }
    case "poll": return [{ type: "text", text: `[投票] ${content.title}\n${content.options.map((option, index) => `${index + 1}. ${option.title}`).join("\n")}` }];
    case "effect": return normalizeContent(content.content, save, depth + 1);
    // Control events are never turned into chatbot prompts.
    default: return [];
  }
}

export function mentionParts(message: Pick<IMessageMessage, "content" | "nativeText" | "mentions">, parts: WirePart[]): WirePart[] {
  if (message.content.type !== "text" || message.content.text !== message.nativeText || !message.mentions?.length) return parts;
  const text = message.content.text;
  const result: WirePart[] = [];
  let end = 0;
  for (const mention of [...message.mentions].sort((a, b) => a.start - b.start)) {
    if (mention.start < end || mention.start + mention.length > text.length) continue;
    if (mention.start > end) result.push({ type: "text", text: text.slice(end, mention.start) });
    result.push({ type: "mention", sender_id: mention.address, text: text.slice(mention.start, mention.start + mention.length) });
    end = mention.start + mention.length;
  }
  if (end < text.length) result.push({ type: "text", text: text.slice(end) });
  return result;
}

export function cacheKey(phone: string, chatId: string, messageId: string): string {
  return JSON.stringify([phone, chatId, messageId]);
}

export function rememberMessage(cache: Map<string, Message>, message: Message, phone: string, chatId: string): void {
  cache.set(cacheKey(phone, chatId, message.id), message);
  while (cache.size > 1000) cache.delete(cache.keys().next().value!);
}

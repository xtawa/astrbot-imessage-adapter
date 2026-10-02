import assert from "node:assert/strict";
import { mkdtemp, readFile, readdir, rm } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { test } from "node:test";
import { attachment, group, markdown, poll, reply, text, voice, type Content, type Message, type Space } from "@spectrum-ts/core";
import { imessage, type IMessageMessage } from "@spectrum-ts/imessage";
import { cacheKey, mentionParts, normalizeContent, saveMedia } from "./content.js";
import { buildPart, PartialDeliveryError, requestSchema, safeError, sendParts } from "./protocol.js";

test("real SDK text, markdown, group and reply normalization", async () => {
  const target = { id: "old", content: await text("quoted").build(), sender: { id: "peer" } } as Message;
  const grouped = await group("A", markdown("**B**")).build();
  const parts = await normalizeContent(grouped, async () => { throw new Error("unused"); });
  assert.deepEqual(parts.map(part => part.text), ["A", "**B**"]);
  const replied = await reply("answer", target).build();
  const result = await normalizeContent(replied, async () => { throw new Error("unused"); });
  assert.equal(result[0]?.target_id, "old");
  assert.equal(result[1]?.text, "answer");
});

test("SDK attachments and voices use bounded streams and safe filenames", async () => {
  const directory = await mkdtemp(join(tmpdir(), "photon-media-"));
  try {
    const file = await attachment(Buffer.from("hello"), { name: "../../a.png", mimeType: "image/png" }).build();
    assert.equal(file.type, "attachment");
    const saved = await saveMedia(file as Extract<Content, { type: "attachment" }>, directory, 10, 100);
    assert.equal((await readFile(saved.path!)).toString(), "hello");
    assert.ok(saved.path!.startsWith(directory));
    const audio = await voice(Buffer.from("audio"), { name: "voice.m4a", mimeType: "audio/mp4" }).build();
    const parts = await normalizeContent(audio, value => saveMedia(value, directory, 2, 100));
    assert.match(parts[0]!.text!, /附件无法下载/);
    assert.equal((await readdir(directory)).filter(name => name.endsWith(".part")).length, 0);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test("stream size is limited even without declared size", async () => {
  const directory = await mkdtemp(join(tmpdir(), "photon-limit-"));
  try {
    const file = { type: "attachment", id: "a", name: "x.bin", mimeType: "application/octet-stream", stream: async () => new ReadableStream({ start(controller) { controller.enqueue(Buffer.from("123456")); controller.close(); } }) } as Extract<Content, { type: "attachment" }>;
    await assert.rejects(saveMedia(file, directory, 4, 100), /attachment_limit/);
    assert.deepEqual(await readdir(directory), []);
  } finally { await rm(directory, { recursive: true, force: true }); }
});

test("native UTF-16 mentions preserve emoji before mention", () => {
  const message: Pick<IMessageMessage, "content" | "nativeText" | "mentions"> = { content: { type: "text", text: "😀 @bot hi" }, nativeText: "😀 @bot hi", mentions: [{ address: "bot", start: 3, length: 4 }] };
  const parts = mentionParts(message, []);
  assert.equal(parts[0]?.text, "😀 ");
  assert.equal(parts[1]?.sender_id, "bot");
  assert.equal(parts[2]?.text, " hi");
});

test("control events do not trigger chatbot messages", async () => {
  const parts = await normalizeContent({ type: "typing", state: "start" }, async () => { throw new Error("unused"); });
  assert.deepEqual(parts, []);
});

test("outbound builders match pinned provider effects, links and polls", async () => {
  const built = buildPart({ type: "text", text: "hello", markdown: true }, "lasers");
  assert.notEqual(typeof built, "string");
  const effectContent = typeof built === "string" ? undefined : await built.build();
  assert.equal(effectContent?.type, "effect");
  if (effectContent?.type === "effect") assert.equal(effectContent.effect, imessage.effect.message.lasers);
  const link = buildPart({ type: "text", text: "https://example.com", link_preview: true });
  assert.equal(typeof link !== "string" && (await link.build()).type, "richlink");
  const voting = buildPart({ type: "poll", title: "Choose", options: ["A", "B"] });
  assert.equal(typeof voting !== "string" && (await voting.build()).type, "poll");
  assert.throws(() => buildPart({ type: "voice", path: "a.m4a", name: "a.m4a", mime_type: "audio/mp4" }, "lasers"), /unsupported_effect_content/);
});

test("send validates all media before any delivery", async () => {
  let sends = 0;
  const space = { send: async () => { sends++; return { id: "sent" }; } } as unknown as Space;
  const request = requestSchema.parse({ id: 1, action: "send", parts: [{ type: "text", text: "hi" }, { type: "attachment", path: "missing.png", name: "x.png", mime_type: "image/png" }] });
  await assert.rejects(sendParts(space, request, async () => { throw new Error("missing"); }));
  assert.equal(sends, 0);
});

test("threaded replies and partial sends retain IDs without retry", async () => {
  let sends = 0;
  const target = { reply: async () => { sends++; if (sends === 2) throw new Error("network"); return { id: "first" }; } };
  const space = { getMessage: async () => target, send: async () => { throw new Error("wrong send path"); } } as unknown as Space;
  const request = requestSchema.parse({ id: 1, action: "send", reply_id: "old", parts: [{ type: "text", text: "A" }, { type: "text", text: "B" }] });
  await assert.rejects(sendParts(space, request, async () => {}), error => error instanceof PartialDeliveryError && error.sentIds[0] === "first");
  assert.equal(sends, 2);
});

test("polls normalize and credential-bearing errors are not exposed", async () => {
  const parts = await normalizeContent(await poll("选择", ["A", "B"]).build(), async () => { throw new Error("unused"); });
  assert.match(parts[0]!.text!, /1\. A/);
  assert.ok(!safeError(new Error("secret=very-private text=private-message")).includes("private"));
  assert.notEqual(cacheKey("line1", "chat", "id"), cacheKey("line2", "chat", "id"));
});

test("schema rejects unknown commands and empty or oversized polls", () => {
  assert.throws(() => requestSchema.parse({ id: 1, action: "shell" }));
  assert.throws(() => requestSchema.parse({ id: 1, action: "send", parts: [{ type: "poll", title: "x", options: ["one"] }] }));
});

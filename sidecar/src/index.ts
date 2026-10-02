import { stat } from "node:fs/promises";
import { once } from "node:events";
import { createInterface } from "node:readline";
import { Spectrum, type Message } from "@spectrum-ts/core";
import { imessage } from "@spectrum-ts/imessage";
import { cacheKey, mentionParts, normalizeContent, rememberMessage, saveMedia } from "./content.js";
import { PartialDeliveryError, requestSchema, safeError, sendParts, type Request } from "./protocol.js";

async function main(): Promise<void> {
  const directory = process.env.PHOTON_MEDIA_DIR;
  if (!directory || !process.env.PHOTON_PROJECT_ID || !process.env.PHOTON_PROJECT_SECRET) throw new Error("missing_config");
  const maxBytes = Number(process.env.PHOTON_MAX_BYTES ?? 20 * 1024 * 1024);
  const cacheBytes = Number(process.env.PHOTON_CACHE_BYTES ?? 512 * 1024 * 1024);
  const app = await Spectrum({
    projectId: process.env.PHOTON_PROJECT_ID, projectSecret: process.env.PHOTON_PROJECT_SECRET,
    providers: [imessage.config()], options: { logLevel: "silent" }, telemetry: false,
  });
  const provider = imessage(app);
  const cache = new Map<string, Message>();
  const write = async (data: unknown): Promise<void> => {
    if (!process.stdout.write(JSON.stringify(data) + "\n")) await once(process.stdout, "drain");
  };
  const validateMedia = async (path: string): Promise<void> => {
    const info = await stat(path);
    if (!info.isFile() || info.size > maxBytes) throw new Error("attachment_limit");
  };
  const dispatch = async (request: Request): Promise<unknown> => {
    if (request.action === "ping") return { ready: true, protocol: 1 };
    if (request.action === "create_chat") {
      if (!request.addresses?.length) throw new Error("addresses_required");
      const users = await Promise.all(request.addresses.map(address => provider.user(address)));
      const space = await provider.space.create(users.length === 1 ? users[0]! : users, request.phone ? { phone: request.phone } : undefined);
      return { chat_id: space.id, phone: space.phone, kind: space.type };
    }
    if (!request.route) throw new Error("route_required");
    const route = request.route;
    const space = await provider.space.get(route.chat_id, route.phone ? { phone: route.phone } : undefined);
    if (space.type !== route.kind) throw new Error("group_required");
    if (request.action === "send") {
      return sendParts(space, request, validateMedia);
    }
    if (request.action === "typing") {
      await (request.active ? space.startTyping() : space.stopTyping()); return null;
    }
    if (request.action === "share_contact") { await space.shareContactCard(); return null; }
    if (request.action === "avatar" || request.action === "background") {
      if (!request.path) throw new Error("attachment_limit");
      if (request.path !== "clear") await validateMedia(request.path);
      if (request.action === "avatar") {
        if (space.type !== "group") throw new Error("group_required");
        await space.avatar(request.path, { mimeType: request.mime_type });
      } else await space.background(request.path, { mimeType: request.mime_type });
      return null;
    }
    if (["group_info", "rename", "add_members", "remove_members", "leave"].includes(request.action)) {
      if (space.type !== "group") throw new Error("group_required");
      if (request.action === "group_info") {
        const [name, members] = await Promise.all([space.getDisplayName(), space.getMembers()]);
        return { name, members: members.map(user => ({ id: user.id })) };
      }
      if (request.action === "rename") {
        if (!request.name) throw new Error("name_required");
        await space.rename(request.name);
      } else if (request.action === "leave") await space.leave();
      else {
        if (!request.members?.length) throw new Error("members_required");
        await (request.action === "add_members" ? space.add(request.members) : space.remove(request.members));
      }
      return null;
    }
    const message = request.message_id ? cache.get(cacheKey(space.phone, space.id, request.message_id)) ?? await space.getMessage(request.message_id) : undefined;
    if (!message) throw new Error("message_not_found");
    if (request.action === "react") {
      if (!request.emoji) throw new Error("emoji_required");
      const reaction = await message.react(request.emoji);
      if (!reaction) throw new Error("provider_returned_no_message");
      rememberMessage(cache, reaction, space.phone, space.id);
      return reaction.id;
    }
    if (request.action === "read") {
      if (message.direction !== "inbound") throw new Error("inbound_message_required");
      await message.read(); return null;
    }
    if (message.direction !== "outbound") throw new Error("outbound_message_required");
    if (request.action === "unsend") await message.unsend();
    else if (request.action === "edit") {
      if (!request.text) throw new Error("text_required");
      await message.edit(request.text);
    }
    return null;
  };
  const input = createInterface({ input: process.stdin, crlfDelay: Infinity });
  // All delivery operations are ordered; inbound reads run independently.
  let commands = Promise.resolve();
  input.on("line", line => {
    commands = commands.then(async () => {
      let id: number | undefined;
      try {
        const raw: unknown = JSON.parse(line);
        if (raw && typeof raw === "object" && "id" in raw && typeof raw.id === "number") id = raw.id;
        const request = requestSchema.parse(raw);
        await write({ id: request.id, result: await dispatch(request) });
      } catch (error) {
        await write({ id, error: error instanceof PartialDeliveryError ? error.message : safeError(error), sent_ids: error instanceof PartialDeliveryError ? error.sentIds : [] });
      }
    });
    commands.catch(() => { process.exitCode = 1; input.close(); });
  });
  let stopping = false;
  const stop = async (): Promise<void> => {
    if (stopping) return;
    stopping = true;
    input.close();
    await app.stop();
  };
  input.on("close", () => { void stop(); });
  process.once("SIGTERM", () => { void stop(); });
  process.once("SIGINT", () => { void stop(); });
  await write({ event: "ready", protocol: 1 });
  try {
    for await (const [space, rawMessage] of app.messages) {
      if (rawMessage.direction !== "inbound" || !rawMessage.sender?.id) continue;
      if (space.__platform !== "imessage") continue;
      const message = imessage(rawMessage);
      if (!message) continue;
      const phone = message.space.phone;
      if (cache.has(cacheKey(phone, space.id, message.id))) continue;
      try {
        const parts = mentionParts(message, await normalizeContent(message.content, value => saveMedia(value, directory, maxBytes, cacheBytes)));
        if (!parts.length) continue;
        rememberMessage(cache, rawMessage, phone, space.id);
        await write({ event: "message", message: {
          id: message.id, chat_id: space.id, phone, kind: message.space.type,
          sender_id: message.sender?.id, direction: message.direction,
          timestamp: Math.floor((message.timestamp ?? new Date()).getTime() / 1000),
          group_name: message.groupTitle, parts,
        } });
      } catch { await write({ event: "warning", code: "inbound_content_failed" }); }
    }
  } finally { await stop(); }
}

main().catch(() => { process.stderr.write("Photon bridge initialization or stream failed\n"); process.exitCode = 1; });

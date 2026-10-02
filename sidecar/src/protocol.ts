import { z } from "zod";
import { app, attachment, contact, markdown, poll, richlink, voice, type ContentInput, type Space } from "@spectrum-ts/core";
import { effect, imessage } from "@spectrum-ts/imessage";

export const routeSchema = z.object({
  chat_id: z.string().min(1).max(4096), phone: z.string().max(256), kind: z.enum(["dm", "group"]),
});
export type WireRoute = z.infer<typeof routeSchema>;
const partSchema = z.discriminatedUnion("type", [
  z.object({ type: z.literal("text"), text: z.string().min(1).max(20000), markdown: z.boolean().optional(), link_preview: z.boolean().optional() }),
  z.object({ type: z.enum(["attachment", "voice"]), path: z.string().min(1), name: z.string().min(1), mime_type: z.string().min(1) }),
  z.object({ type: z.literal("contact"), vcard: z.string().min(1).max(100000) }),
  z.object({ type: z.literal("poll"), title: z.string().min(1).max(300), options: z.array(z.string().min(1)).min(2).max(10) }),
  z.object({ type: z.literal("app"), url: z.url(), caption: z.string().min(1).max(1024), subcaption: z.string().max(1024).optional() }),
]);
export const requestSchema = z.object({
  id: z.number().int().nonnegative(), action: z.enum(["send", "react", "edit", "unsend", "read", "typing", "group_info", "rename", "add_members", "remove_members", "leave", "create_chat", "ping", "avatar", "background", "share_contact"]),
  route: routeSchema.optional(), parts: z.array(partSchema).max(100).optional(),
  reply_id: z.string().optional(), effect: z.string().optional(),
  message_id: z.string().optional(), emoji: z.string().max(64).optional(),
  text: z.string().max(20000).optional(), active: z.boolean().optional(), name: z.string().max(1024).optional(),
  members: z.array(z.string().min(1)).min(1).max(100).optional(),
  addresses: z.array(z.string().min(1)).min(1).max(100).optional(), phone: z.string().optional(),
  path: z.string().optional(), mime_type: z.string().optional(),
});
export type Request = z.infer<typeof requestSchema>;

export class PartialDeliveryError extends Error {
  constructor(public readonly sentIds: string[], cause: unknown) {
    super(safeError(cause));
  }
}

export function buildPart(part: z.infer<typeof partSchema>, effectName = ""): ContentInput {
  let content: ContentInput;
  switch (part.type) {
    case "text":
      if (part.link_preview && /^https?:\/\/\S+$/.test(part.text)) content = richlink(part.text);
      else content = part.markdown ? markdown(part.text) : part.text;
      break;
    case "attachment": content = attachment(part.path, { name: part.name, mimeType: part.mime_type }); break;
    case "voice": content = voice(part.path, { name: part.name, mimeType: part.mime_type }); break;
    case "contact": content = contact(part.vcard); break;
    case "poll": content = poll(part.title, part.options); break;
    case "app": content = app(part.url, { layout: { caption: part.caption, subcaption: part.subcaption } }); break;
  }
  if (effectName) {
    const effects = imessage.effect.message;
    if (!(effectName in effects)) throw new Error("unknown_effect");
    // Provider effect() cannot wrap voice/contact/richlink content.
    if (!["text", "attachment"].includes(part.type) || (part.type === "text" && part.link_preview && /^https?:\/\/\S+$/.test(part.text))) throw new Error("unsupported_effect_content");
    content = effect(content, effects[effectName as keyof typeof effects]);
  }
  return content;
}

export async function sendParts(space: Space, request: Request, validateMedia: (path: string) => Promise<void>): Promise<string[]> {
  if (!request.parts?.length) throw new Error("parts_required");
  // Resolve targets and validate ALL content before the first delivery.
  const target = request.reply_id ? await space.getMessage(request.reply_id) : undefined;
  if (request.reply_id && !target) throw new Error("reply_target_not_found");
  const built: ContentInput[] = [];
  for (const part of request.parts) {
    if (part.type === "attachment" || part.type === "voice") await validateMedia(part.path);
    const content = buildPart(part, request.effect ?? "");
    if (typeof content !== "string") await content.build();
    built.push(content);
  }
  const ids: string[] = [];
  for (const content of built) {
    try {
      const result = target ? await target.reply(content) : await space.send(content);
      if (!result) throw new Error("provider_returned_no_message");
      ids.push(result.id);
    } catch (error) {
      if (ids.length) throw new PartialDeliveryError(ids, error);
      throw error;
    }
  }
  return ids;
}

export function safeError(error: unknown): string {
  const value = error as { code?: unknown; message?: unknown };
  const known = new Set(["parts_required", "reply_target_not_found", "unknown_effect", "unsupported_effect_content", "provider_returned_no_message", "message_not_found", "outbound_message_required", "inbound_message_required", "group_required", "addresses_required", "route_required", "text_required", "emoji_required", "members_required", "name_required", "attachment_limit"]);
  if (typeof value?.message === "string" && known.has(value.message)) return value.message;
  if (typeof value?.code === "number") return `Photon RPC failed (code ${value.code})`;
  return "Photon operation failed; check project permissions, target registration, network, and Apple operation limits";
}

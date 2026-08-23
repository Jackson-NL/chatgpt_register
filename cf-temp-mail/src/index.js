/**
 * Cloudflare 临时邮箱 Worker
 *
 * 功能：
 * 1. email handler：接收发往任意地址的邮件（Email Routing catch-all），
 *    提取 6 位验证码，存入 KV（key = 收件地址）
 * 2. fetch handler：HTTP API 查询验证码
 *
 * 部署：
 * - wrangler.toml 配置 KV namespace + email routes
 * - Cloudflare Dashboard: Email Routing → Catch-all → Send to Worker
 */
export default {
  async email(message, env, ctx) {
    try {
      const to = message.to; // 收件地址，如 foo@yourdomain.com
      const from = message.from;
      const raw = await new Response(message.raw).text();

      // 提取验证码：优先找 6 位数字（邮件正文里通常有 "code is 123456" 之类）
      let code = "";
      const bodyText = raw
        .replace(/<[^>]*>/g, " ")          // 去 HTML 标签
        .replace(/&amp;/g, "&")
        .replace(/&lt;/g, "<")
        .replace(/&gt;/g, ">")
        .replace(/&nbsp;/g, " ")
        .replace(/=0D=0A|=3D/g, " ")
        .replace(/\s+/g, " ");
      const codeMatch = bodyText.match(/(?:code|verification|confirm)[^\d]{0,40}?(\d{6})/i)
        || bodyText.match(/\b(\d{6})\b/);
      if (codeMatch) code = codeMatch[1];

      const payload = {
        to,
        from,
        code,
        receivedAt: Date.now(),
        subject: message.headers.get("subject") || "",
        snippet: bodyText.slice(0, 800),
      };
      await env.CF_TEMP_MAIL.put(to.toLowerCase(), JSON.stringify(payload));
      console.log(`[email] ${to} code=${code || "none"}`);
    } catch (err) {
      console.error("[email] error:", err);
    }
  },

  async fetch(request, env) {
    const url = new URL(request.url);
    const cors = {
      "Access-Control-Allow-Origin": "*",
      "Access-Control-Allow-Methods": "GET, OPTIONS",
      "Access-Control-Allow-Headers": "Content-Type",
    };
    if (request.method === "OPTIONS") {
      return new Response(null, { headers: cors });
    }

    const address = url.searchParams.get("address");
    if (!address) {
      return new Response(JSON.stringify({ error: "missing address param" }), {
        status: 400,
        headers: { ...cors, "Content-Type": "application/json" },
      });
    }

    const data = await env.CF_TEMP_MAIL.get(address.toLowerCase());
    if (!data) {
      return new Response(JSON.stringify({ code: null, received: false }), {
        status: 200,
        headers: { ...cors, "Content-Type": "application/json" },
      });
    }
    return new Response(data, {
      status: 200,
      headers: { ...cors, "Content-Type": "application/json" },
    });
  },
};

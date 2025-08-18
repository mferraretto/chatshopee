# src/duoke.py
import inspect
import asyncio
import os
import re
import json
from pathlib import Path
from playwright.async_api import async_playwright, Error as PwError
from .config import settings

# Carrega seletores configuráveis
SEL = json.loads(
    (Path(__file__).resolve().parents[1] / "config" / "selectors.json")
    .read_text(encoding="utf-8")
)

class DuokeBot:
    def __init__(self, storage_state_path: str = "storage_state.json"):
        # Mantido por compat
        self.storage_state_path = storage_state_path
        # Página atual (usada pelo espelho da UI)
        self.current_page = None

    # ---------- infra de navegador ----------

    async def _new_context(self, p):
        """
        Contexto persistente: mantém cookies/localStorage dentro de 'pw-user-data'.
        Em produção (Render), iniciamos em headless e sem sandbox.
        """
        user_data_dir = Path(__file__).resolve().parents[1] / "pw-user-data"
        user_data_dir.mkdir(exist_ok=True)

        # HEADLESS=1 (padrão) para servidores sem display; HEADLESS=0 no dev local
        headless = os.getenv("HEADLESS", "1").lower() not in {"0", "false", "no"}

        ctx = await p.chromium.launch_persistent_context(
    user_data_dir=str(user_data_dir),
    headless=True,  # <- headless no Render
    args=[
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--disable-gpu",
    ],
)

        return ctx

    async def _get_page(self, ctx):
        page = ctx.pages[0] if ctx.pages else await ctx.new_page()
        self.current_page = page
        return page

    async def ensure_login(self, page):
        await page.goto(settings.douke_url, wait_until="domcontentloaded")
        # Aguarda rede “assentar”
        try:
            await page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass

        # Espera aparecer lista de chats OU a UL principal de mensagens
        chat_list_container = SEL.get("chat_list_container", "")
        chat_list_item = SEL.get("chat_list_item", "ul.chat_list li")
        try:
            if chat_list_container:
                await page.wait_for_selector(
                    f"{chat_list_container}, {chat_list_item}, ul.message_main",
                    timeout=60000,
                )
            else:
                await page.wait_for_selector(
                    f"{chat_list_item}, ul.message_main",
                    timeout=60000,
                )
        except Exception:
            print("Aviso: elementos de chat não apareceram em 60s; seguindo assim mesmo.")

    async def apply_needs_reply_filter(self, page):
        if not getattr(settings, "apply_needs_reply_filter", False):
            return
        try:
            sel = SEL.get("filter_needs_reply", "")
            if not sel:
                return
            locator = page.locator(sel)
            if await locator.count() > 0:
                await locator.first.click()
                await page.wait_for_timeout(1000)
        except Exception:
            pass

    # ---------- navegação entre conversas ----------

    def conversations(self, page):
        return page.locator(SEL.get("chat_list_item", "ul.chat_list li"))

    async def open_conversation_by_index(self, page, idx: int) -> bool:
        conv_locator = self.conversations(page)
        total = await conv_locator.count()
        if idx >= total:
            return False

        await conv_locator.nth(idx).click()

        # Aguarda painel renderizar
        try:
            if SEL.get("message_container"):
                await page.wait_for_selector(SEL["message_container"], timeout=15000)
            await page.wait_for_function(
                """() => {
                    const ul = document.querySelector('ul.message_main');
                    return ul && ul.children && ul.children.length > 0;
                }""",
                timeout=15000
            )
        except Exception:
            pass

        try:
            if SEL.get("input_textarea"):
                await page.wait_for_selector(SEL["input_textarea"], timeout=8000)
        except Exception:
            pass

        await page.wait_for_timeout(int(getattr(settings, "delay_between_actions", 1.0) * 1000))
        return True

    # ---------- leitura de mensagens ----------

    async def read_messages_with_roles(self, page, depth: int) -> list[tuple[str, str]]:
        """Retorna últimos N [(role,text)], role ∈ {'buyer','seller'}."""
        out: list[tuple[str, str]] = []
        try:
            items = page.locator("ul.message_main > li")

            # Força mais histórico: rola ao topo algumas vezes
            try:
                container = page.locator(SEL.get("message_container", "ul.message_main")).first
                for _ in range(3):
                    await container.evaluate("(el) => { el.scrollTop = 0; }")
                    await page.wait_for_timeout(120)
            except Exception:
                pass

            texts = await items.evaluate_all("""
                (els) => els.map(li => {
                    const cls = (li.className || '').toLowerCase();
                    const role = cls.includes('lt') ? 'buyer' : (cls.includes('rt') ? 'seller' : 'system');
                    const txtNode = li.querySelector('div.text_cont, .bubble .text, .record_item .content');
                    const txt = (txtNode?.innerText || '').trim();
                    return txt && role !== 'system' ? [role, txt] : null;
                }).filter(Boolean)
            """)
            out = texts[-depth:]
        except Exception:
            pass
        return out

    async def read_messages(self, page, depth: int = 8) -> list[str]:
        """Compat: apenas textos do comprador."""
        msgs: list[str] = []
        container = page.locator(SEL.get("message_container", "ul.message_main")).first
        if not await container.count():
            print("[DEBUG] Nenhum container de mensagens encontrado")
            return msgs

        for _ in range(3):
            try:
                await container.evaluate("(el) => { el.scrollTop = 0; }")
                await page.wait_for_timeout(60)
            except Exception:
                break

        buyer_sel = SEL.get("buyer_message", "ul.message_main li.lt .text_cont")
        try:
            nodes = page.locator(buyer_sel)
            msgs = await nodes.evaluate_all(
                "(els) => els.map(el => (el.innerText || '').trim()).filter(Boolean)"
            )
            print(f"[DEBUG] Mensagens do cliente encontradas: {len(msgs)}")
            return msgs[-depth:]
        except Exception as e:
            print(f"[DEBUG] erro ao extrair mensagens com evaluate_all: {e}")
            return []

    # ---------- painel lateral (pedido) ----------

    async def read_sidebar_order_info(self, page) -> dict:
        """Extrai status, orderId, título, variação, SKU e campos rotulados do painel de pedido."""
        return await page.evaluate("""
        () => {
          const norm = (s) => (s || '').replace(/\\s+/g, ' ').trim();

          const panels = Array.from(document.querySelectorAll('div,section,article'));
          let right = panels.find(el => /Buyer payment amount|Payment Time|Variation:|Varia[cç][aã]o:|SKU\\s*:/i.test(el.textContent || ''));
          if (!right) right = document.body;

          let statusNode =
            right.querySelector('[class*="order_item_status_tags"] .el-tag, .el-tag.el-tag--warning, .el-tag--success, .el-tag--info, .el-tag') ||
            Array.from(right.querySelectorAll('span')).find(s => {
              const t = norm(s.textContent || '');
              return t && t.length <= 32 && /shipped|enviado|to ship|a caminho|entregue|ready to ship|to return|returned|cancelado|canceled/i.test(t);
            }) || null;
          const status = norm(statusNode && statusNode.textContent) || '';

          const allText = norm(right.textContent || '');
          let orderId = '';
          const hashId = allText.match(/#([A-Z0-9]{8,})\\b/);
          const plainId = allText.match(/\\b[0-9A-Z]{10,}\\b/);
          if (hashId && hashId[1]) orderId = hashId[1];
          else if (plainId) orderId = plainId[0];

          const candidates = Array.from(right.querySelectorAll('div,section,article'));
          const scored = candidates.map(el => {
            const t = el.textContent || '';
            const score =
              (/SKU\\s*:/i.test(t) ? 1 : 0) +
              (/(Variation|Varia[cç][aã]o)\\s*:/i.test(t) ? 1 : 0) +
              (/Buyer payment amount/i.test(t) ? 1 : 0) +
              (/Payment Time/i.test(t) ? 1 : 0) +
              (el.querySelector('.product_name, .order_item, .order_title, .dk_msg_order') ? 1 : 0);
            return { el, score, len: t.length };
          }).filter(x => x.score > 0).sort((a,b)=> b.score - a.score || b.len - a.len);
          const card = (scored[0] && scored[0].el) || right;

          let titleNode =
            card.querySelector('.product_name, [class*="product_name"], .line_clamp_2, a[title]') ||
            card.querySelector('a, [class*="title"], [class*="products_item"]') ||
            card;
          let title = '';
          if (titleNode) {
            const lines = norm(titleNode.textContent).split('\\n').map(norm).filter(Boolean);
            title = lines[0] || '';
          }

          const cardText = card.textContent || '';
          const vMatch = cardText.match(/(?:Variation|Varia[cç][aã]o)\\s*:\\s*(.+)/i);
          const variation = norm((vMatch && vMatch[1] || '').split('\\n')[0]);

          const sMatch = cardText.match(/\\bSKU\\s*:\\s*([A-Za-z0-9\\-\\._]+)/i);
          const sku = norm((sMatch && sMatch[1]) || '');

          const fields = {};
          (right.querySelectorAll('*') || []).forEach(el => {
            const t = norm(el.textContent);
            const m = t.match(/^([^:]{3,}):\\s*(.+)$/);
            if (m) {
              const key = norm(m[1]);
              const val = norm(m[2]);
              if (key && val && key.length <= 64) fields[key] = val;
            }
          });

          return { status, orderId, title, variation, sku, fields };
        }
        """)

    # ---------- envio de resposta ----------

    async def send_reply(self, page, text: str):
        candidates = [s.strip() for s in SEL.get("input_textarea", "").split(",") if s.strip()]
        box = None

        for sel in candidates:
            loc = page.locator(sel).first
            try:
                await loc.wait_for(state="visible", timeout=5000)
                if await loc.is_enabled():
                    box = loc
                    break
            except Exception:
                continue

        if not box:
            try:
                box = page.get_by_placeholder(
                    re.compile(r"Type a message here|press Enter to send|Enter to send", re.I)
                ).first
                await box.wait_for(state="visible", timeout=3000)
            except Exception:
                raise RuntimeError("Campo de mensagem não encontrado (todos candidatos estavam ocultos).")

        await box.click()
        try:
            await box.fill(text)
        except Exception:
            await box.type(text, delay=4)

        await page.keyboard.press("Enter")

        try:
            btn_sel = SEL.get("send_button", "")
            if btn_sel:
                btn = page.locator(btn_sel)
                if await btn.count() > 0:
                    await btn.first.click()
        except Exception:
            pass

    # ---------- utilidades ----------

    async def maybe_extract_tracking(self, page) -> str | None:
        try:
            content = await page.content()
        except Exception:
            return None
        m = re.search(r"\b([A-Z]{2}\d{8,}[A-Z0-9]{1,})\b", content or "")
        return m.group(1) if m else None

    # ---------- modos de execução ----------

    async def _cycle(self, page, decide_reply_fn):
        """Executa um ciclo sobre as conversas visíveis."""
        await self.apply_needs_reply_filter(page)

        conv_locator = self.conversations(page)
        await page.wait_for_timeout(300)
        total = await conv_locator.count()
        print(f"[DEBUG] conversas visíveis: {total}")

        max_convs = int(getattr(settings, "max_conversations", 0) or 0)
        if max_convs > 0:
            total = min(total, max_convs)

        for i in range(total):
            try:
                ok = await self.open_conversation_by_index(page, i)
                if not ok:
                    continue
            except Exception as e:
                print(f"[DEBUG] falha ao abrir conversa {i}: {e}")
                continue

            try:
                order_info = await self.read_sidebar_order_info(page)
                print("[DEBUG] Order info:", order_info)
            except Exception as e:
                order_info = {}
                print(f"[DEBUG] falha ao ler order_info: {e}")

            pairs = await self.read_messages_with_roles(page, int(getattr(settings, "history_depth", 5) or 5))
            print(f"[DEBUG] conversa {i}: {len(pairs)} msgs (com role)")
            if not pairs:
                continue

            # responder apenas se a última mensagem for do comprador
            last_role, _ = pairs[-1]
            if last_role != "buyer":
                print("[DEBUG] pulando: última mensagem não é do comprador")
                continue

            buyer_only = [t for r, t in pairs if r == "buyer"]

            should = False
            reply = ""
            try:
                params = inspect.signature(decide_reply_fn).parameters
                if len(params) >= 2:
                    result = decide_reply_fn(pairs, buyer_only)
                else:
                    result = decide_reply_fn(buyer_only)
                if inspect.isawaitable(result):
                    result = await result
                should, reply = result
            except Exception as e:
                print(f"[DEBUG] erro no hook/classificador: {e}")
                continue

            print(f"[DEBUG] decide: should={should} | Resposta: {reply}")
            if not should:
                continue

            if order_info.get("status"):
                if "status:" not in reply.lower():
                    reply += f"\n\n_Status atual do pedido:_ **{order_info['status']}**"
            if order_info.get("orderId") and "{ORDER_ID}" in reply:
                reply = reply.replace("{ORDER_ID}", order_info["orderId"])

            tracking = await self.maybe_extract_tracking(page)
            if tracking and "aplicativo da Shopee" in reply:
                reply = reply.replace(
                    "aplicativo da Shopee",
                    f"aplicativo da Shopee (código {tracking})"
                )

            await self.send_reply(page, reply)
            await page.wait_for_timeout(int(getattr(settings, "delay_between_actions", 1.0) * 1000))

    async def run_once(self, decide_reply_fn):
        """Modo pontual (mantido por compat)."""
        async with async_playwright() as p:
            ctx = await self._new_context(p)
            page = await self._get_page(ctx)
            await self.ensure_login(page)
            await self._cycle(page, decide_reply_fn)
            print("[DEBUG] Execução concluída. Mantendo o navegador aberto por ~60s...")
            await asyncio.sleep(60)
            try:
                await ctx.close()
            finally:
                self.current_page = None

    async def run_forever(self, decide_reply_fn, idle_seconds: float = 3.0):
        """
        Loop infinito, com auto-recuperação.
        Use este método a partir do app_ui (start/stop via task).
        """
        while True:
            ctx = None
            try:
                async with async_playwright() as p:
                    ctx = await self._new_context(p)
                    page = await self._get_page(ctx)
                    await self.ensure_login(page)

                    while True:
                        await self._cycle(page, decide_reply_fn)
                        await asyncio.sleep(idle_seconds)

            except asyncio.CancelledError:
                try:
                    if ctx:
                        await ctx.close()
                finally:
                    self.current_page = None
                break
            except PwError as e:
                print(f"[ERROR] Playwright: {e}. Reiniciando em 2s...")
                await asyncio.sleep(2)
                continue
            except Exception as e:
                print(f"[ERROR] run_forever: {e}. Tentando novamente em 2s...")
                await asyncio.sleep(2)
                continue
            finally:
                try:
                    if ctx:
                        await ctx.close()
                except Exception:
                    pass
                self.current_page = None

#!/usr/bin/env python3
"""tools/gen_anchors.py on synthetic Java/C#/Nest/Odoo sources. Run: python3 tests/test_anchors.py
(Real mono-backend code is not available offline: check the output of tools/setup.sh against a few known routes.)"""
import subprocess, sys, tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / 'tools'))
import gen_anchors

K = Path(tempfile.mkdtemp(prefix='dcs-anchors-'))


def repo(name, files):
    d = K / name
    subprocess.run(['git', 'init', '-q', str(d)], check=True)
    for rel, text in files.items():
        p = d / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding='utf-8')
    subprocess.run(['git', '-C', str(d), '-c', 'user.name=T', '-c', 'user.email=t@x.io', 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(d), '-c', 'user.name=T', '-c', 'user.email=t@x.io', 'commit', '-qm', 'i'], check=True)


repo('mono-backend', {
    'java/order/CartController.java': '''package x;
@RestController
@RequestMapping("/api/v1/app/carts")
public class CartController {
    @PostMapping("/my-open-cart/delivery-options")
    @Operation(summary = "x")
    public ResponseEntity<Object> deliveryOptions(@RequestBody Req r) {
        return ok(mediator.send(new GetDeliveryOptionsQuery(r)));
    }
    @GetMapping(value = "/my-open-cart")
    public Cart open() { return null; }
}
''',
    'java/order/GetDeliveryOptionsQueryHandler.java': 'package x;\npublic class GetDeliveryOptionsQueryHandler {}\n',
    'java/order/ErrorCode.java': '''public enum ErrorCode {
    VOUCHER_NOT_IN_PROGRESS(4064, "Khuyến mãi không tồn tại hoặc không khả dụng"),
    OTHER(5001, "x");
}
''',
    'java/order/ClaimHandler.java': '''public class ClaimHandler {
    void run() {
        throw new DomainException(ErrorCode.VOUCHER_NOT_IN_PROGRESS);
    }
}
''',
    'java/order/Cart.java': '@Entity\n@Table(name = "carts")\npublic class Cart {}\n',
    'csharp/Promo/VoucherController.cs': '''[Route("api/v1/vouchers")]
public class VoucherController : ControllerBase
{
    [HttpPost("claim")]
    public async Task<IActionResult> Claim([FromBody] ClaimVoucherCommand c)
    {
        return Ok(await _mediator.Send(new ClaimVoucherCommand(c)));
    }
}
public static class Codes { public const int Ended = 4070; }
''',
    'csharp/Promo/ClaimVoucherCommandHandler.cs': 'public class ClaimVoucherCommandHandler {}\n',
    'java/order/test/FakeTest.java': '@GetMapping("/should-not-appear")\npublic void t() {}\n',
    # gateway-prefixed services (the real layout is <lang>/services/<name>)
    'csharp/services/marketing-service/Controllers/LeadController.cs': '''[Route("v1/app/marketing/leads")]
public class LeadController : ControllerBase
{
    [HttpGet]
    public async Task<IActionResult> GetLeads() { return Ok(); }
    [HttpPost("v2/app/marketing/lead-categories/{id}/suspend")]
    public async Task<IActionResult> Suspend(string id) { return Ok(); }
}
''',
    'java/services/order-service/Placeholder.java': 'public class Placeholder {}\n',
    # NestJS: class prefix + a method whose decorators span several lines (the handler is getBalance, not SwaggerResponse)
    'typescript/services/gamification-service/src/reward.controller.ts': '''@Controller('back-office/rewards')
@ApiTags('rewards')
export class RewardController {
  @Get(':id/balance')
  @SwaggerResponse({
    status: 200,
    description: 'balance (x)',
  })
  async getBalance(@Param('id') id: string) {
    return this.mediator.send(new GetBalanceQuery(id));
  }
}
''',
    # EF migration snapshots repeat every table: must not flood tables.md
    'csharp/services/marketing-service/Migrations/20260101_Init.Designer.cs': 'b.ToTable("leads_snapshot_only");\n',
})
repo('droppii-odoo', {
    'develop/addons/x/controllers/main.py': '''class Main(http.Controller):
    @http.route('/api/stock/check', type='json', auth='user')
    def check_stock(self, **kw):
        return {}
''',
    'develop/addons/x/models/m.py': "class M(models.Model):\n    _name = 'stock.picking.x'\n",
})

repo('biz-mobile', {
    'src/locales/vi.json': '{\n  "cart": {\n    "voucherEnded": "Khuyến mãi không tồn tại hoặc không khả dụng",\n    "empty": "Giỏ hàng trống"\n  }\n}\n',
    'src/screens/CartScreen.tsx': "export function CartScreen() {\n  const { t } = useTranslation();\n  return <Text>{t('cart.voucherEnded')}</Text>;\n}\n",
    'src/api/cart.ts': """export const getDeliveryOptions = (id: string) =>
  http.post('/carts/my-open-cart/delivery-options', { id });
export const claimVoucher = (code: string) => api.post(`/vouchers/claim`, { code });
export const onError = (e) => { if (e.code === 4064) { showVoucherEnded(); } };
""",
    'src/api/consts.ts': "export const ORDER_SUMMARY = '/api/v1/app/orders/summary';\n",
    'src/navigation/Root.tsx': '<Stack.Screen name="Cart" component={CartScreen} />\n',
    'node_modules/x/ignored.ts': "http.get('/should/not/appear/in/api/calls');\n",
    # real shapes: RN resource object, endpoint constants (string + arrow fn), generics in the call
    'src/translation/resources/common_vi.ts': "export default {\n  common: {\n    c_skip: 'Bỏ qua',\n    voucher_ended: 'Khuyến mãi không tồn tại hoặc không khả dụng',\n  },\n};\n",
    'src/services/configurations.ts': """const END_POINTS = deepFreeze({
  MARKETING: {
    GET_LEADS: '/marketing-service/v1/app/marketing/leads',
    STOP_BOOK_LEAD: (id: string) =>
      `/marketing-service/v2/app/marketing/lead-categories/${id}/suspend`,
  },
  ORDER: {
    SUMMARY: '/order-service/v1/app/orders/summary',
  },
});
""",
    'src/api/leads.ts': """export const getLeads = () => apiInstance.get<BaseResponse<Lead[]>>(END_POINTS.MARKETING.GET_LEADS);
export const stopLead = (id: string) => apiInstance.post(END_POINTS.MARKETING.STOP_BOOK_LEAD(id));
export const summary = () => apiInstance.get(END_POINTS.ORDER.SUMMARY);
export const gone = () => apiInstance.get(END_POINTS.NOPE.MISSING);
""",
})
repo('b2b-client', {
    'src/common.translations.json': '{\n  "namespaces": "common",\n  "note_ok": {\n    "key": "common:note_update_success",\n    "messages": {\n      "vi-VN": "\'Ghi chú cập nhật thành công!\'",\n      "en-US": "Note updated"\n    }\n  }\n}\n',
    'src/components/Note.tsx': "const msg = t('note_update_success');\n",
})
repo('recommender-service', {'app.py': '@app.get("/recommend/{user_id}")\nasync def recommend(user_id: str):\n    return []\n'})
repo('openim-server', {'internal/api/route.go': 'func Register(r *gin.Engine) {\n\tr.POST("/msg/send", api.SendMsg)\n}\n'})
repo('web-mall', {
    'app/(shop)/products/[id]/page.tsx': "export default function ProductPage() {}\nconst r = await fetch('/api/v1/app/carts/my-open-cart', { method: 'GET' });\n",
    'app/api/health/route.ts': 'export function GET() {}\n',
})
import json as _json
(K.parent / 'config.json').write_text(_json.dumps({"knowledge": [
    {"name": "mono-backend", "branch": "dev", "layer": "be"}, {"name": "droppii-odoo", "branch": "dev", "layer": "odoo"},
    {"name": "biz-mobile", "branch": "main", "layer": "mobile"}, {"name": "web-mall", "branch": "main", "layer": "fe"},
    {"name": "b2b-client", "branch": "main", "layer": "fe"}, {"name": "recommender-service", "branch": "main", "layer": "be"},
    {"name": "openim-server", "branch": "dev", "layer": "be"}]}))
out = K / '_anchors'
stats = gen_anchors.run(K, out)
routes = (out / 'routes.md').read_text(encoding='utf-8')
errors = (out / 'errors.md').read_text(encoding='utf-8')
tables = (out / 'tables.md').read_text(encoding='utf-8')
versions = (out / 'versions.md').read_text(encoding='utf-8')

# Spring: class prefix + method path, handler method, dispatched Query and its handler class file
assert '| POST | /api/v1/app/carts/my-open-cart/delivery-options |' in routes and 'deliveryOptions' in routes, routes
assert 'GetDeliveryOptionsQuery' in routes and 'mono-backend/java/order/GetDeliveryOptionsQueryHandler.java:2' in routes, routes
assert '| GET | /api/v1/app/carts/my-open-cart |' in routes, routes
# ASP.NET: [Route] prefix + [HttpPost]
assert '| POST | /api/v1/vouchers/claim |' in routes and 'ClaimVoucherCommandHandler.cs' in routes, routes
# Odoo
assert '/api/stock/check' in routes and 'check_stock' in routes, routes
# test dirs are skipped
assert 'should-not-appear' not in routes
# error codes -> defined at + referenced at
assert '| 4064 | VOUCHER_NOT_IN_PROGRESS | Khuyến mãi không tồn tại hoặc không khả dụng | mono-backend/java/order/ErrorCode.java:2 |' in errors, errors
assert 'ClaimHandler.java:3' in errors and '| 4070 | Ended |' in errors, errors
# tables, versions
assert '| carts | Cart |' in tables and 'stock.picking.x' in tables, tables
assert 'mono-backend' in versions and 'dev' in versions and stats['routes'] >= 4, (versions, stats)

# ---- FE / mobile ----
uitexts = (out / 'ui-texts.md').read_text(encoding='utf-8')
apicalls = (out / 'api-calls.md').read_text(encoding='utf-8')
edges = (out / 'edges.md').read_text(encoding='utf-8')
screens = (out / 'screens.md').read_text(encoding='utf-8')
emap = (out / 'error-map.md').read_text(encoding='utf-8')
# the UI text a ticket quotes -> key -> where defined -> the component that uses it
assert ('| Khuyến mãi không tồn tại hoặc không khả dụng | vi | cart.voucherEnded | biz-mobile/src/locales/vi.json:3 | '
        'biz-mobile/src/screens/CartScreen.tsx:3 |') in uitexts, uitexts
# RN resource object: ns:dotted key from the file name, locale from the suffix; next-intl *.translations.json: key + messages per locale,
# wrapping quotes in the value are dropped, and the call site t('name') finds the namespaced key
assert '| Khuyến mãi không tồn tại hoặc không khả dụng | vi | common:common.voucher_ended | biz-mobile/src/translation/resources/common_vi.ts:4 |' in uitexts, uitexts
assert '| Ghi chú cập nhật thành công! | vi | common:note_update_success | b2b-client/src/common.translations.json:3 | b2b-client/src/components/Note.tsx:1 |' in uitexts, uitexts
assert '| Note updated | en | common:note_update_success |' in uitexts, uitexts
# calls: base URL-less path, template literal path, fetch with method, path constants outside a call; node_modules ignored
assert '| POST | /carts/my-open-cart/delivery-options | biz-mobile/src/api/cart.ts:2 | getDeliveryOptions |' in apicalls, apicalls
assert '| POST | /vouchers/claim | biz-mobile/src/api/cart.ts:3 | claimVoucher |' in apicalls, apicalls
assert '| GET | /api/v1/app/carts/my-open-cart | web-mall/app/(shop)/products/[id]/page.tsx:2 |' in apicalls, apicalls
assert '| ? | /api/v1/app/orders/summary | biz-mobile/src/api/consts.ts:1 |' in apicalls and 'should/not/appear' not in apicalls, apicalls
# the cross-repo edges: client call -> backend route -> handler, with confidence
assert ('| biz-mobile/src/api/cart.ts:2 | getDeliveryOptions | POST | /carts/my-open-cart/delivery-options | medium | '
        'mono-backend/java/order/CartController.java:7 | deliveryOptions | GetDeliveryOptionsQuery | '
        'mono-backend/java/order/GetDeliveryOptionsQueryHandler.java:2 |') in edges, edges
assert '| POST | /vouchers/claim | medium | mono-backend/csharp/Promo/VoucherController.cs' in edges, edges
assert '| GET | /api/v1/app/carts/my-open-cart | high | mono-backend/java/order/CartController.java:11 |' in edges, edges
# screens: React Navigation + Next.js route (group dropped, [id] -> :id, api routes skipped)
assert '| Cart | CartScreen | biz-mobile/src/navigation/Root.tsx:1 | biz-mobile/src/screens/CartScreen.tsx:1 |' in screens, screens
assert '| /products/:id | page | web-mall/app/(shop)/products/[id]/page.tsx:1 |' in screens and '/health' not in screens, screens
# backend error code <-> UI text <-> client handler
assert '| 4064 | VOUCHER_NOT_IN_PROGRESS | Khuyến mãi không tồn tại hoặc không khả dụng |' in emap, emap
assert 'cart.voucherEnded (biz-mobile/src/locales/vi.json:3)' in emap and 'common:common.voucher_ended' in emap, emap
assert 'biz-mobile/src/api/cart.ts:4' in emap, emap
# client code is not scanned as backend: no bogus routes from it, and the versions table has the layer
assert 'biz-mobile' not in routes and 'web-mall' not in routes
assert '| biz-mobile | mobile |' in versions and '| mono-backend | be |' in versions, versions
assert stats['ui_texts'] >= 6 and stats['edges'] >= 3 and stats['screens'] == 2, stats

# endpoint constants (the real mobile style): resolved through the object, generics tolerated, gateway prefix ignored
apicalls = (out / 'api-calls.md').read_text(encoding='utf-8')
edges = (out / 'edges.md').read_text(encoding='utf-8')
assert '| GET | /marketing-service/v1/app/marketing/leads | biz-mobile/src/api/leads.ts:1 | getLeads |' in apicalls, apicalls
assert '| POST | /marketing-service/v2/app/marketing/lead-categories/${id}/suspend | biz-mobile/src/api/leads.ts:2 | stopLead |' in apicalls, apicalls
assert 'NOPE' not in apicalls and 'MISSING' not in apicalls                       # unresolved constant: no invented path
# ... and joined to the C# routes (class [Route] + bare [HttpGet]; method-level full path), exact => high
assert ('| biz-mobile/src/api/leads.ts:1 | getLeads | GET | /marketing-service/v1/app/marketing/leads | high | '
        'mono-backend/csharp/services/marketing-service/Controllers/LeadController.cs:5 | GetLeads |') in edges, edges
# (the class-level [Route] is a prefix for the method path, so the server path is longer than the client's: tail match = medium)
assert ('| biz-mobile/src/api/leads.ts:2 | stopLead | POST | /marketing-service/v2/app/marketing/lead-categories/${id}/suspend | medium | '
        'mono-backend/csharp/services/marketing-service/Controllers/LeadController.cs:7 | Suspend |') in edges, edges
# no route found but the gateway prefix names the service: still tell the brain which folder to open
assert ('| biz-mobile/src/api/leads.ts:3 | summary | GET | /order-service/v1/app/orders/summary | service | '
        'mono-backend/java/services/order-service |') in edges, edges
# Strapi-style calls whose host is a variable never match a controller by accident
assert 'STRAPI' not in edges
# service catalog: language + code dir + routes found; other backend repos are one service each; Go / FastAPI routes
services = (out / 'services.md').read_text(encoding='utf-8')
routes = (out / 'routes.md').read_text(encoding='utf-8')
assert '| marketing-service | csharp | mono-backend/csharp/services/marketing-service | 2 |' in services, services
assert '| gamification-service | typescript | mono-backend/typescript/services/gamification-service | 1 |' in services, services
assert '| order-service | java | mono-backend/java/services/order-service | 0 |' in services, services
assert '| recommender-service | be | recommender-service | 1 |' in services and '| openim-server | be | openim-server | 1 |' in services, services
assert '| GET | /recommend/{user_id} | recommender-service/app.py:2 | recommend |' in routes, routes
assert ('| GET | /back-office/rewards/:id/balance | mono-backend/typescript/services/gamification-service/src/reward.controller.ts:9 | '
        'getBalance | GetBalanceQuery |') in routes, routes
assert 'SwaggerResponse' not in routes
assert '| POST | /msg/send | openim-server/internal/api/route.go:2 | SendMsg |' in routes, routes
# EF snapshot files are ignored for tables
assert 'leads_snapshot_only' not in (out / 'tables.md').read_text(encoding='utf-8')

# pure helpers
import anchors_client as ac
assert ac.norm_path('https://api.x.io/v1/Orders/${id}/items?x=1') == '/v1/orders/{}/items'
assert ac.norm_path('/carts/:cartId/') == '/carts/{}'
assert ac.match_route('GET', '/{}', [('GET', '/a/b', 'l', 's', '', '')]) == []          # a bare param matches nothing
assert ac.match_route('GET', '/{}/api/certs', [('GET', '/{}/{}/certs', 'l', 's', '', '')]) == []   # wildcards on both sides prove nothing
assert ac.match_route('GET', '${BASE}/api/certs', [('GET', '/x/api/certs', 'l', 's', '', '')])[0][0] == 'medium'  # base-URL variable is stripped
assert ac.match_route('GET', '/x-service/v1/a/b', [('GET', '/api/v1/a/b', 'l', 's', '', '')])[0][0] == 'medium'
assert ac.match_route('GET', '/only', [('GET', '/only', 'l', 's', '', '')]) == []                  # one literal segment is too weak
assert ac.service_for('/order-service/v1/x', {'order-service': 'r/o'}) == 'r/o' and ac.service_for('/v1/x', {'order-service': 'r/o'}) == ''
assert ac.clean_text(" 'Xin chào' ") == 'Xin chào' and ac.clean_text('It\'s') == "It's"
consts = ac.scan_constants("""const END_POINTS = deepFreeze({
  A: {
    B: '/svc/b',
    C: (id: string) =>
      `/svc/c/${id}`,
    NESTED: { D: '/svc/d' },
  },
  NOT_A_PATH: 'hello',
});""")
assert consts == {'END_POINTS': {'A.B': '/svc/b', 'A.C': '/svc/c/${id}', 'A.NESTED.D': '/svc/d'}}, consts
assert ac.match_route('GET', '/a/b', [('POST', '/a/b', 'l', 's', '', '')]) == []          # verb must agree
assert ac.match_route('ANY', '/a/b', [('POST', '/x/a/b', 'l', 's', '', '')])[0][0] == 'medium'
many = [('GET', f'/p{i}/a/b', 'l', 's', '', '') for i in range(5)]
assert {c for c, _ in ac.match_route('GET', '/a/b', many)} == {'low'}                    # ambiguous tail = low confidence
assert ac.next_route(('app', '(g)', 'x', '[id]', 'page.tsx')) == '/x/:id' and ac.next_route(('app', 'api', 'r', 'page.tsx')) is None
assert ac.next_route(('pages', 'index.tsx')) == '/' and ac.next_route(('pages', '_app.tsx')) is None
print('ok anchors', stats)

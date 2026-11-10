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
})
repo('droppii-odoo', {
    'develop/addons/x/controllers/main.py': '''class Main(http.Controller):
    @http.route('/api/stock/check', type='json', auth='user')
    def check_stock(self, **kw):
        return {}
''',
    'develop/addons/x/models/m.py': "class M(models.Model):\n    _name = 'stock.picking.x'\n",
})

(K.parent / 'config.json').write_text('{"knowledge": [{"name": "mono-backend", "branch": "dev"}, {"name": "droppii-odoo", "branch": "dev"}]}')
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
print('ok anchors', stats)

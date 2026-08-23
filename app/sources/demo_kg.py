'''DemoProvider: a clearly labelled demo dataset. NOT a production integration.

Every record is stored with is_demo = 1, badged in the UI and flagged in exports.
It exists so the product is fully usable and testable without a paid directory
contract. DEMO_WEBSITE_BASE points the demo websites at a server you control
(the test suite points it at the local fixture server).
'''
from __future__ import annotations
import os
from .base import LeadSource, RawRecord, register
from ..domain import taxonomy
from ..domain.normalize import normalize_city

D = 'Открыли новый филиал'
DATASET = [
    {'id': 'demo-1', 'name': 'ОсОО Кафе Альфа', 'category': 'ресторан', 'city': 'Бишкек', 'phone': '0555 11 22 33',
     'website': '/good/', 'instagram': 'cafe.alfa.kg', 'branches': 3, 'employees': 25, 'address': 'пр. Чуй 120',
     'description': 'Кафе и доставка еды. Открыли новый филиал в августе, идет набор персонала.'},
    {'id': 'demo-2', 'name': 'Кафе Альфа', 'category': 'cafe', 'city': 'bishkek', 'phone': '+996 555 112233',
     'website': '/good/', 'address': 'Чуй 120', 'description': 'Та же компания из другого каталога (дубликат)'},
    {'id': 'demo-3', 'name': 'СТО Турбо', 'category': 'сто', 'city': 'Ош', 'phone': '0312 900 900',
     'website': '/bad/', 'address': 'ул. Ленина 5', 'employees': 8,
     'description': 'Автосервис, ремонт двигателей. Заявки принимаем только по телефону.'},
    {'id': 'demo-4', 'name': 'Стоматология Денталь', 'category': 'клиника', 'city': 'Бишкек', 'phone': '0700 44 55 66',
     'instagram': 'dental.kg', 'whatsapp': '0700 44 55 66', 'branches': 2, 'employees': 14,
     'address': 'ул. Советская 10',
     'description': 'Стоматология. Открываем второй филиал, набираем врачей. Запись только через Instagram.'},
    {'id': 'demo-5', 'name': 'Мебельный цех Югурт', 'category': 'производство', 'city': 'Ош', 'phone': '0770 12 34 56',
     'instagram': 'yugurt.mebel', 'employees': 30, 'address': 'Ош, промзона',
     'description': 'Производство мебели на заказ. Активная реклама в Instagram, каталога нет.'},
    {'id': 'demo-6', 'name': 'Строительная компания Курулуш', 'category': 'строительство', 'city': 'Бишкек',
     'phone': '0312 55 66 77', 'email': 'info@kurulush.example', 'employees': 120, 'branches': 2,
     'address': 'ул. Ахунбаева 97',
     'description': 'Строительство жилых комплексов. Запуск нового ЖК, активная реклама.'},
    {'id': 'demo-7', 'name': 'Гостиница Ала-Тоо', 'category': 'гостиница', 'city': 'Каракол', 'phone': '0555 99 00 11',
     'website': '/malformed', 'address': 'Каракол, ул. Гебера 5', 'employees': 12,
     'description': 'Гостиница у Иссык-Куля. Сайт старый, бронирование только по телефону.'},
    {'id': 'demo-8', 'name': 'Оптовый склад Алтын', 'category': 'опт', 'city': 'Бишкек', 'phone': '0559 88 77 66',
     'telegram': 'altyn_opt', 'employees': 45, 'branches': 4, 'address': 'Дордой, ряд 12',
     'description': 'Оптовая торговля текстилем. Заказы ведем вручную, есть Telegram-канал.'},
    {'id': 'demo-9', 'name': 'Language center Bilim', 'category': 'образовательный центр', 'city': 'Бишкек',
     'phone': '0777 10 20 30', 'instagram': 'bilim.center', 'whatsapp': '0777 10 20 30', 'employees': 18,
     'address': 'ул. Токтогула 125',
     'description': 'Курсы английского. Набор на осенние группы, активная реклама.'},
    {'id': 'demo-10', 'name': 'Автомойка Aqua', 'category': 'автомойка', 'city': 'Токмок', 'phone': '0500 33 22 11',
     'address': 'Токмок, трасса', 'employees': 5, 'description': 'Автомойка и детейлинг.'},
    {'id': 'demo-11', 'name': 'Магазин Дайыр', 'category': 'магазин', 'city': 'Джалал-Абад', 'phone': '0772 45 67 89',
     'instagram': 'daiyr.shop', 'address': 'Джалал-Абад, центр', 'branches': 2,
     'description': 'Магазин одежды. Продаем только через Instagram, сайта нет.'},
    {'id': 'demo-12', 'name': 'Клиника МедиКер', 'category': 'медцентр', 'city': 'Ош', 'phone': '0550 60 70 80',
     'website': '/error500', 'address': 'Ош, ул. Курманжан Датка 42', 'employees': 40,
     'description': 'Многопрофильный медцентр. Сайт не работает.'},
    {'id': 'demo-13', 'name': 'Барбершоп Kesme', 'category': 'барбершоп', 'city': 'Бишкек', 'phone': '0708 11 11 11',
     'instagram': 'kesme.barber', 'whatsapp': '0708 11 11 11', 'employees': 6, 'branches': 3,
     'address': 'ул. Московская 60',
     'description': 'Сеть барбершопов, 3 точки. Запись вручную в WhatsApp.'},
    {'id': 'demo-14', 'name': 'Логистика KG Cargo', 'category': 'логистика', 'city': 'Бишкек', 'phone': '0312 11 22 33',
     'email': 'ops@kgcargo.example', 'telegram': 'kgcargo', 'employees': 60, 'address': 'аэропорт Манас',
     'description': 'Грузоперевозки Китай-КГ. Трекинг заявок вручную.'},
    {'id': 'demo-15', 'name': 'ОсОО Альфа Строй', 'category': 'строительство', 'city': 'Бишкек', 'phone': '0555 44 33 22',
     'address': 'ул. Жибек Жолу 200', 'employees': 35,
     'description': 'Отделочные работы. Не путать с Кафе Альфа: другая компания.'},
    {'id': 'demo-16', 'name': 'Швейный цех Айчүрөк', 'category': 'производство', 'city': 'Бишкек', 'phone': '0709 55 44 33',
     'instagram': 'aichurok.textile', 'employees': 80, 'address': 'ул. Лев Толстого 17',
     'description': 'Швейное производство, экспорт. Расширяем цех, набор швей.'},
    {'id': 'demo-17', 'name': 'Coffee Room', 'category': 'кафе', 'city': 'Бишкек', 'phone': '0555 77 88 99',
     'instagram': 'coffeeroom.kg', 'website': '/slow/', 'employees': 9, 'address': 'ул. Исанова 42',
     'description': 'Кофейня. Сайт очень медленный.'},
    {'id': 'demo-18', 'name': 'Автозапчасти Деталь', 'category': 'автозапчасти', 'city': 'Ош', 'phone': '0773 22 33 44',
     'address': 'Ош, авторынок', 'branches': 2,
     'description': 'Продажа автозапчастей, открыли вторую точку.'},
    {'id': 'demo-19', 'name': 'Гостиница Ош Плаза', 'category': 'гостиница', 'city': 'Ош', 'phone': '0322 55 44 33',
     'email': 'book@oshplaza.example', 'employees': 55, 'address': 'Ош, центр',
     'description': 'Гостиница в центре Оша. Бронирование только по почте.'},
    {'id': 'demo-20', 'name': 'Магазин СтройМаркет', 'category': 'магазин', 'city': 'Бишкек', 'phone': '0555 12 12 12',
     'whatsapp': '0555 12 12 12', 'branches': 5, 'employees': 70, 'address': 'ул. Горького 1',
     'description': 'Строительные материалы, 5 филиалов. Нет каталога и онлайн-заказа.'},
]

@register
class DemoProvider(LeadSource):
    name = 'demo_kg'
    is_demo = True
    description = 'Labelled demo dataset of Kyrgyz businesses (no external calls)'

    def available(self):
        return True, 'demo dataset always available'

    def _website(self, path):
        if not path:
            return None
        if str(path).startswith('http'):
            return path
        base = os.environ.get('DEMO_WEBSITE_BASE', '').rstrip('/')
        return (base + path) if base else None

    def fetch(self, query, limit=50):
        wanted_cities = set(query.cities or [])
        wanted_categories = set(query.categories or [])
        emitted = 0
        for row in DATASET:
            if emitted >= limit:
                return
            category = taxonomy.canonical_category(row.get('category'))
            if wanted_categories and category not in wanted_categories:
                continue
            city = normalize_city(row.get('city'))[0]
            if wanted_cities and city not in wanted_cities:
                continue
            self.limiter.wait()
            emitted += 1
            yield RawRecord(
                source=self.name, external_id=row['id'], company_name=row['name'],
                source_url='demo://' + self.name + '/' + row['id'], is_demo=True,
                category=row.get('category'), city=row.get('city'), phone=row.get('phone'),
                email=row.get('email'), website=self._website(row.get('website')),
                instagram=row.get('instagram'), telegram=row.get('telegram'),
                whatsapp=row.get('whatsapp'), address=row.get('address'),
                description=row.get('description'), employees_estimate=row.get('employees'),
                branches_estimate=row.get('branches'), extra={'dataset': 'demo_kg_v1'},
            ).sanitized()

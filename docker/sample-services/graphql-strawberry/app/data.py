from __future__ import annotations

from dataclasses import dataclass

from faker import Faker

_SEED = 20261012
_CUSTOMER_SEGMENTS = ["Enterprise", "Startup", "Government", "SMB"]
_CUSTOMER_STATUSES = ["Active", "Inactive"]
_PRODUCT_STATUSES = ["Active", "Discontinued"]
_MATERIAL_GROUPS = ["MG-SAFTY", "MG-PHARM", "MG-ELECT", "MG-APPRL"]


@dataclass
class Customer:
    customer_id: str
    first_name: str
    last_name: str
    email: str
    city: str
    country: str
    customer_segment: str
    status: str


@dataclass
class Product:
    material_number: str
    material_description: str
    material_group: str
    price: float
    currency: str
    status: str


def _generate_customers(fake: Faker, count: int) -> list[Customer]:
    customers = []
    for i in range(1, count + 1):
        customers.append(
            Customer(
                customer_id=f"CUST-{i:06d}",
                first_name=fake.first_name(),
                last_name=fake.last_name(),
                email=fake.email(),
                city=fake.city(),
                country=fake.country_code(),
                customer_segment=fake.random_element(_CUSTOMER_SEGMENTS),
                status=fake.random_element(_CUSTOMER_STATUSES),
            )
        )
    return customers


def _generate_products(fake: Faker, count: int) -> list[Product]:
    products = []
    for i in range(1, count + 1):
        products.append(
            Product(
                material_number=f"MAT-{i:06d}",
                material_description=fake.catch_phrase(),
                material_group=fake.random_element(_MATERIAL_GROUPS),
                price=round(fake.pyfloat(min_value=5, max_value=500, right_digits=2), 2),
                currency="USD",
                status=fake.random_element(_PRODUCT_STATUSES),
            )
        )
    return products


_fake = Faker()
_fake.seed_instance(_SEED)

CUSTOMERS: list[Customer] = _generate_customers(_fake, 20)
PRODUCTS: list[Product] = _generate_products(_fake, 20)


def find_customer(customer_id: str) -> Customer | None:
    return next((c for c in CUSTOMERS if c.customer_id == customer_id), None)


def find_product(material_number: str) -> Product | None:
    return next((p for p in PRODUCTS if p.material_number == material_number), None)

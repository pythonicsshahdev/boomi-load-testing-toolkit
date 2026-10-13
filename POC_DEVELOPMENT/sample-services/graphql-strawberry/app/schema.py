from __future__ import annotations

import strawberry

from app.data import CUSTOMERS, PRODUCTS, find_customer, find_product


@strawberry.type
class Customer:
    customer_id: str
    first_name: str
    last_name: str
    email: str
    city: str
    country: str
    customer_segment: str
    status: str


@strawberry.type
class Product:
    material_number: str
    material_description: str
    material_group: str
    price: float
    currency: str
    status: str


def _to_graphql_customer(c) -> Customer:
    return Customer(
        customer_id=c.customer_id,
        first_name=c.first_name,
        last_name=c.last_name,
        email=c.email,
        city=c.city,
        country=c.country,
        customer_segment=c.customer_segment,
        status=c.status,
    )


def _to_graphql_product(p) -> Product:
    return Product(
        material_number=p.material_number,
        material_description=p.material_description,
        material_group=p.material_group,
        price=p.price,
        currency=p.currency,
        status=p.status,
    )


@strawberry.type
class Query:
    @strawberry.field
    def customers(self, limit: int = 10) -> list[Customer]:
        return [_to_graphql_customer(c) for c in CUSTOMERS[:limit]]

    @strawberry.field
    def customer(self, customer_id: str) -> Customer | None:
        found = find_customer(customer_id)
        return _to_graphql_customer(found) if found else None

    @strawberry.field
    def products(self, limit: int = 10) -> list[Product]:
        return [_to_graphql_product(p) for p in PRODUCTS[:limit]]

    @strawberry.field
    def product(self, material_number: str) -> Product | None:
        found = find_product(material_number)
        return _to_graphql_product(found) if found else None


schema = strawberry.Schema(query=Query)

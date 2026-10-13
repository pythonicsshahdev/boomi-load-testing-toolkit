from fastapi.testclient import TestClient

from app.main import app

client = TestClient(app)


def test_ping():
    response = client.get("/ping")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_customers_query_returns_fixtures():
    query = """
    query {
        customers(limit: 3) {
            customerId
            firstName
            email
        }
    }
    """
    response = client.post("/graphql", json={"query": query})
    assert response.status_code == 200
    body = response.json()
    assert "errors" not in body
    customers = body["data"]["customers"]
    assert len(customers) == 3
    assert customers[0]["customerId"] == "CUST-000001"


def test_customer_query_by_id_matches_list_entry():
    list_query = "query { customers(limit: 1) { customerId } }"
    list_response = client.post("/graphql", json={"query": list_query})
    customer_id = list_response.json()["data"]["customers"][0]["customerId"]

    detail_query = """
    query($id: String!) {
        customer(customerId: $id) {
            customerId
            firstName
            lastName
            email
        }
    }
    """
    detail_response = client.post(
        "/graphql", json={"query": detail_query, "variables": {"id": customer_id}}
    )
    detail = detail_response.json()["data"]["customer"]
    assert detail["customerId"] == customer_id


def test_customer_query_unknown_id_returns_null():
    query = """
    query {
        customer(customerId: "CUST-999999") {
            customerId
        }
    }
    """
    response = client.post("/graphql", json={"query": query})
    body = response.json()
    assert "errors" not in body
    assert body["data"]["customer"] is None


def test_products_query_returns_fixtures():
    query = "query { products(limit: 2) { materialNumber price currency } }"
    response = client.post("/graphql", json={"query": query})
    body = response.json()
    assert "errors" not in body
    assert len(body["data"]["products"]) == 2

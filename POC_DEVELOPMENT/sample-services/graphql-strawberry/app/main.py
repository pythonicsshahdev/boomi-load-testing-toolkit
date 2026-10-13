from fastapi import FastAPI
from strawberry.fastapi import GraphQLRouter

from app.schema import schema

app = FastAPI(title="Boomi Performance Testing — GraphQL Sample Target")

graphql_app = GraphQLRouter(schema)
app.include_router(graphql_app, prefix="/graphql")


@app.get("/ping")
def ping() -> dict[str, str]:
    return {"status": "ok"}

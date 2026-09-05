# Synthetic Test Project

A tiny, **non-proprietary** Java + SQL project used to develop and regression-test
CodeXray (build plan §66). Proprietary source cannot be committed, so every
analyzer capability is exercised here instead.

## Pattern coverage

| Build-plan pattern | Where |
|--------------------|-------|
| A — SQL from a literal constant | `QueryConstants.GET_CUSTOMER`, `CustomerRepository.findActiveCustomerIds` |
| B — SQL via a constants class | `CustomerRepository.findCustomer` → `QueryConstants.GET_CUSTOMER` |
| C — table name from metadata | `MetadataService.getPhysicalTable` → `META_TABLE_REGISTRY` |
| D — columns from metadata | `MetadataService.getColumnList` → `META_COLUMN_REGISTRY` |
| E — SQL assembled from variables | `DynamicQueryBuilder.buildWithConcat` |
| F — string concatenation | `DynamicQueryBuilder.buildWithConcat` |
| G — StringBuilder | `DynamicQueryBuilder.buildWithBuilder` |
| H — method-generated SQL | `DynamicQueryBuilder.buildQuery` |
| I — SQL through multiple methods | `CustomerService.processCustomer` → `loadEligibleCustomers` → builder/repo |
| J — SQL/config values | `application.properties` (`metadata.tableRegistry`, …) |
| Unresolved runtime value | `loadEligibleCustomers` table depends on runtime metadata → expect `PARTIALLY_RESOLVED` |
| Secret redaction | `application.properties` `db.password`, `db.connectionString` |

## End-to-end trace (build plan §83)

```
CustomerService.processCustomer
  -> CustomerService.loadEligibleCustomers
       -> MetadataService.getPhysicalTable  (QueryConstants.TABLE_LOOKUP_SQL -> META_TABLE_REGISTRY)
       -> MetadataService.getColumnList     (QueryConstants.COLUMN_LOOKUP_SQL -> META_COLUMN_REGISTRY)
       -> DynamicQueryBuilder.buildQuery -> buildWithConcat  (SELECT <cols> FROM <table> WHERE STATUS = ?)
       -> CustomerRepository.runResolvedQuery
```

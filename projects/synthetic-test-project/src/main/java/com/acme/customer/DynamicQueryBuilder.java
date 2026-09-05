package com.acme.customer;

import java.util.List;

/**
 * Purpose:        Assemble SQL at runtime from a metadata-derived table name and
 *                 column list.
 * Responsibility: Exercise build-plan Patterns E / F / G / H:
 *                   - buildWithConcat():  String + String concatenation
 *                   - buildWithBuilder(): StringBuilder.append(...) chain
 *                   - buildQuery():       method-generated SQL entry point
 * Notes:          The SELECT keyword and WHERE clause are static; the table and
 *                 columns are NOT statically knowable. Expected indexer verdict
 *                 for the produced SQL: PARTIALLY_RESOLVED, with dependencies
 *                 [METADATA_QUERY(table), METADATA_QUERY(columns), PARAMETER(status)].
 */
public class DynamicQueryBuilder {

    private static final String SELECT = "SELECT ";
    private static final String FROM = " FROM ";
    private static final String WHERE = " WHERE STATUS = ?";

    /** Pattern F: pure string concatenation. */
    public String buildWithConcat(String table, List<String> columns) {
        String columnList = String.join(", ", columns);
        return SELECT + columnList + FROM + table + WHERE;
    }

    /** Pattern G: StringBuilder assembly. */
    public String buildWithBuilder(String table, List<String> columns) {
        StringBuilder sql = new StringBuilder();
        sql.append("SELECT ");
        sql.append(String.join(", ", columns));
        sql.append(" FROM ");
        sql.append(table);
        sql.append(" WHERE STATUS = ?");
        return sql.toString();
    }

    /**
     * Pattern H: method-generated SQL. Callers pass metadata; this method decides
     * how the final string is built. Static resolution should follow the chain
     * into {@link #buildWithConcat}.
     */
    public String buildQuery(String table, List<String> columns) {
        return buildWithConcat(table, columns);
    }
}

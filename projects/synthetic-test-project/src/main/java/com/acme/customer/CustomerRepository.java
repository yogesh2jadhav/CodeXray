package com.acme.customer;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.ArrayList;
import java.util.List;

/**
 * Purpose:        Data-access object for the CUSTOMER aggregate.
 * Responsibility: Execute the static queries declared in {@link QueryConstants}
 *                 and one inline literal query, returning simple row maps.
 * Notes:          Exercises indexer features:
 *                   - method-call extraction (prepareStatement, executeQuery ...)
 *                   - SQL stored in a constant (Pattern B) via QueryConstants
 *                   - an inline SQL literal (Pattern A)
 */
public class CustomerRepository {

    private final Connection connection;

    public CustomerRepository(Connection connection) {
        this.connection = connection;
    }

    /** Runs {@link QueryConstants#GET_CUSTOMER}; SQL is resolved via the constant. */
    public List<Object[]> findCustomer(long custId) throws Exception {
        String sql = QueryConstants.GET_CUSTOMER;
        try (PreparedStatement ps = connection.prepareStatement(sql)) {
            ps.setLong(1, custId);
            return toRows(ps.executeQuery());
        }
    }

    /** Inline literal (Pattern A) — a plain SELECT the parser reads directly. */
    public List<Object[]> findActiveCustomerIds() throws Exception {
        String sql = "SELECT CUST_ID FROM CUSTOMER WHERE STATUS = 'ACTIVE'";
        try (PreparedStatement ps = connection.prepareStatement(sql)) {
            return toRows(ps.executeQuery());
        }
    }

    /** Executes an already-built dynamic query string (see DynamicQueryBuilder). */
    public List<Object[]> runResolvedQuery(String resolvedSql) throws Exception {
        try (PreparedStatement ps = connection.prepareStatement(resolvedSql)) {
            return toRows(ps.executeQuery());
        }
    }

    private List<Object[]> toRows(ResultSet rs) throws Exception {
        List<Object[]> rows = new ArrayList<>();
        int cols = rs.getMetaData().getColumnCount();
        while (rs.next()) {
            Object[] row = new Object[cols];
            for (int i = 0; i < cols; i++) {
                row[i] = rs.getObject(i + 1);
            }
            rows.add(row);
        }
        return rows;
    }
}

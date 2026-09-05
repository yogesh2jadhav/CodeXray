package com.acme.customer;

import java.sql.Connection;
import java.sql.PreparedStatement;
import java.sql.ResultSet;
import java.util.ArrayList;
import java.util.List;

/**
 * Purpose:        Resolve *logical* names to *physical* table / column names by
 *                 querying metadata registry tables.
 * Responsibility: Implement build-plan Patterns C and D:
 *                   - getPhysicalTable(): constant SQL -> META_TABLE_REGISTRY -> table name
 *                   - getColumnList():    constant SQL -> META_COLUMN_REGISTRY -> column list
 * Notes:          The runtime return values cannot be known statically. The
 *                 indexer should record the metadata dependency chain and mark
 *                 downstream dynamic SQL as PARTIALLY_RESOLVED — never invent a
 *                 concrete table name.
 */
public class MetadataService {

    private final Connection connection;

    public MetadataService(Connection connection) {
        this.connection = connection;
    }

    /** Pattern C: the physical table name comes from META_TABLE_REGISTRY. */
    public String getPhysicalTable(String logicalName) throws Exception {
        try (PreparedStatement ps = connection.prepareStatement(QueryConstants.TABLE_LOOKUP_SQL)) {
            ps.setString(1, logicalName);
            try (ResultSet rs = ps.executeQuery()) {
                return rs.next() ? rs.getString("PHYSICAL_TABLE") : null;
            }
        }
    }

    /** Pattern D: the column list comes from META_COLUMN_REGISTRY. */
    public List<String> getColumnList(String logicalName) throws Exception {
        List<String> columns = new ArrayList<>();
        try (PreparedStatement ps = connection.prepareStatement(QueryConstants.COLUMN_LOOKUP_SQL)) {
            ps.setString(1, logicalName);
            try (ResultSet rs = ps.executeQuery()) {
                while (rs.next()) {
                    columns.add(rs.getString("COLUMN_NAME"));
                }
            }
        }
        return columns;
    }
}

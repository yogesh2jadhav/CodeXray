package com.acme.customer;

/**
 * Purpose:        Central store of static SQL used across the customer module.
 * Responsibility: Hold SQL as {@code public static final String} constants so the
 *                 indexer can follow {@code QueryConstants.X} indirection
 *                 (build-plan Pattern B) and the SQL parser can analyse the text.
 * Notes:          These are deliberately simple, fully-resolved queries. Dynamic
 *                 assembly lives in {@link DynamicQueryBuilder}.
 */
public final class QueryConstants {

    private QueryConstants() {
        // utility class — not instantiable
    }

    /** Pattern A/B: fully static SELECT with a bind parameter. */
    public static final String GET_CUSTOMER =
        "SELECT CUST_ID, FIRST_NAME, LAST_NAME, STATUS FROM CUSTOMER WHERE CUST_ID = ?";

    /** Pattern C: constant SQL whose *result* supplies a physical table name. */
    public static final String TABLE_LOOKUP_SQL =
        "SELECT PHYSICAL_TABLE FROM META_TABLE_REGISTRY WHERE LOGICAL_NAME = ?";

    /** Pattern D: constant SQL whose result supplies a column list. */
    public static final String COLUMN_LOOKUP_SQL =
        "SELECT COLUMN_NAME FROM META_COLUMN_REGISTRY WHERE LOGICAL_NAME = ? ORDER BY ORDINAL";

    /** Static SELECT with a JOIN — exercises the SQL parser's join extraction. */
    public static final String CUSTOMER_WITH_ADDRESS =
        "SELECT c.CUST_ID, c.LAST_NAME, a.CITY "
        + "FROM CUSTOMER c JOIN CUSTOMER_ADDRESS a ON a.CUST_ID = c.CUST_ID "
        + "WHERE c.STATUS = ?";
}

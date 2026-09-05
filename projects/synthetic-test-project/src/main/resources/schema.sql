-- Purpose:        Schema + representative queries for the synthetic project.
-- Responsibility: Give the SQL parser real DDL and DML to extract tables,
--                 columns, joins, filters and parameters from a .sql file.
-- Notes:          META_* tables back the metadata-driven dynamic SQL flow.

CREATE TABLE CUSTOMER (
    CUST_ID     BIGINT PRIMARY KEY,
    FIRST_NAME  VARCHAR(100),
    LAST_NAME   VARCHAR(100),
    STATUS      VARCHAR(20)
);

CREATE TABLE CUSTOMER_ADDRESS (
    ADDR_ID   BIGINT PRIMARY KEY,
    CUST_ID   BIGINT REFERENCES CUSTOMER(CUST_ID),
    CITY      VARCHAR(100),
    COUNTRY   VARCHAR(100)
);

CREATE TABLE META_TABLE_REGISTRY (
    LOGICAL_NAME   VARCHAR(100) PRIMARY KEY,
    PHYSICAL_TABLE VARCHAR(100)
);

CREATE TABLE META_COLUMN_REGISTRY (
    LOGICAL_NAME VARCHAR(100),
    COLUMN_NAME  VARCHAR(100),
    ORDINAL      INT
);

-- Reporting query with a join and a filter.
SELECT c.CUST_ID, c.LAST_NAME, a.CITY, a.COUNTRY
FROM CUSTOMER c
JOIN CUSTOMER_ADDRESS a ON a.CUST_ID = c.CUST_ID
WHERE c.STATUS = 'ACTIVE' AND a.COUNTRY = :country;

-- Aggregate query.
SELECT STATUS, COUNT(*) AS CUSTOMER_COUNT
FROM CUSTOMER
GROUP BY STATUS;

-- Write statement (INSERT).
INSERT INTO CUSTOMER_ADDRESS (ADDR_ID, CUST_ID, CITY, COUNTRY)
VALUES (?, ?, ?, ?);

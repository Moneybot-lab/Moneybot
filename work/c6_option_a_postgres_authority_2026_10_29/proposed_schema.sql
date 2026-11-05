-- NOT EXECUTED. Separate operator approval, writer exclusion and schema-owner role required.
BEGIN;
SET LOCAL search_path = public, pg_catalog;

CREATE TABLE market_authority_control (
	namespace VARCHAR(128) NOT NULL, 
	body TEXT NOT NULL, 
	PRIMARY KEY (namespace)
)

;

CREATE TABLE market_authority_fields (
	namespace VARCHAR(128) NOT NULL, 
	field VARCHAR(80) NOT NULL, 
	position INTEGER NOT NULL, 
	ordinal BIGINT NOT NULL, 
	body TEXT NOT NULL, 
	PRIMARY KEY (namespace, field), 
	CONSTRAINT market_authority_position_unique UNIQUE (namespace, position)
)

;
COMMIT;

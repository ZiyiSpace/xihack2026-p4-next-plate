import { integer, sqliteTable, text } from "drizzle-orm/sqlite-core";
// Separate demo and live stores. Revision guards make each business action atomic.
export const store = sqliteTable("store", { id:text("id").primaryKey(), revision:integer("revision").notNull().default(0), payload:text("payload").notNull() });

// Secrets stay out of business snapshots, reports and browser responses.
export const modelCredentials = sqliteTable("model_credentials", { id:text("id").primaryKey(), ciphertext:text("ciphertext"), suffix:text("suffix"), disabled:integer("disabled").notNull().default(0), updatedAt:text("updated_at").notNull() });

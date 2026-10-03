CREATE TABLE `model_credentials` (
	`id` text PRIMARY KEY NOT NULL,
	`ciphertext` text,
	`suffix` text,
	`disabled` integer DEFAULT 0 NOT NULL,
	`updated_at` text NOT NULL
);

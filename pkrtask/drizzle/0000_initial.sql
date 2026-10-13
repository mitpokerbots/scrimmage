CREATE TABLE `edges` (
	`id` text PRIMARY KEY NOT NULL,
	`plan_id` text NOT NULL,
	`from_kind` text NOT NULL,
	`from_id` text NOT NULL,
	`to_kind` text NOT NULL,
	`to_id` text NOT NULL,
	FOREIGN KEY (`plan_id`) REFERENCES `plans`(`id`) ON UPDATE no action ON DELETE cascade,
	CONSTRAINT "edges_from_kind" CHECK(from_kind IN ('task', 'plan_link')),
	CONSTRAINT "edges_to_kind" CHECK(to_kind IN ('task', 'plan_link'))
);
--> statement-breakpoint
CREATE UNIQUE INDEX `edges_from_to_unique` ON `edges` (`from_kind`,`from_id`,`to_kind`,`to_id`);--> statement-breakpoint
CREATE TABLE `plan_history` (
	`id` integer PRIMARY KEY AUTOINCREMENT NOT NULL,
	`label` text NOT NULL,
	`plan_id` text,
	`before_state` text,
	`after_state` text,
	`undone` integer DEFAULT false NOT NULL,
	`created_at` integer NOT NULL
);
--> statement-breakpoint
CREATE TABLE `plan_links` (
	`id` text PRIMARY KEY NOT NULL,
	`parent_plan_id` text NOT NULL,
	`child_plan_id` text NOT NULL,
	`x` real DEFAULT 0 NOT NULL,
	`y` real DEFAULT 0 NOT NULL,
	`width` real DEFAULT 360 NOT NULL,
	`height` real DEFAULT 240 NOT NULL,
	`created_at` integer NOT NULL,
	FOREIGN KEY (`parent_plan_id`) REFERENCES `plans`(`id`) ON UPDATE no action ON DELETE cascade,
	FOREIGN KEY (`child_plan_id`) REFERENCES `plans`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE UNIQUE INDEX `plan_links_parent_child_unique` ON `plan_links` (`parent_plan_id`,`child_plan_id`);--> statement-breakpoint
CREATE TABLE `plan_members` (
	`plan_id` text NOT NULL,
	`kerberos` text NOT NULL,
	`role` text DEFAULT 'viewer' NOT NULL,
	PRIMARY KEY(`plan_id`, `kerberos`),
	FOREIGN KEY (`plan_id`) REFERENCES `plans`(`id`) ON UPDATE no action ON DELETE cascade,
	CONSTRAINT "plan_members_role" CHECK(role IN ('viewer', 'editor'))
);
--> statement-breakpoint
CREATE TABLE `plans` (
	`id` text PRIMARY KEY NOT NULL,
	`name` text NOT NULL,
	`description` text DEFAULT '' NOT NULL,
	`owner_kerberos` text NOT NULL,
	`status` text DEFAULT 'todo' NOT NULL,
	`effort` text,
	`deadline` integer,
	`notes` text DEFAULT '' NOT NULL,
	`created_at` integer NOT NULL,
	CONSTRAINT "plans_status" CHECK(status IN ('todo', 'doing', 'done', 'blocked')),
	CONSTRAINT "plans_effort" CHECK("effort" IS NULL OR effort IN ('S', 'M', 'L'))
);
--> statement-breakpoint
CREATE TABLE `task_assignees` (
	`task_id` text NOT NULL,
	`kerberos` text NOT NULL,
	PRIMARY KEY(`task_id`, `kerberos`),
	FOREIGN KEY (`task_id`) REFERENCES `tasks`(`id`) ON UPDATE no action ON DELETE cascade
);
--> statement-breakpoint
CREATE TABLE `tasks` (
	`id` text PRIMARY KEY NOT NULL,
	`plan_id` text NOT NULL,
	`title` text NOT NULL,
	`status` text DEFAULT 'todo' NOT NULL,
	`notes` text DEFAULT '' NOT NULL,
	`deadline` integer,
	`effort` text,
	`x` real DEFAULT 0 NOT NULL,
	`y` real DEFAULT 0 NOT NULL,
	`width` real DEFAULT 240 NOT NULL,
	`height` real DEFAULT 84 NOT NULL,
	`created_at` integer NOT NULL,
	`updated_at` integer NOT NULL,
	FOREIGN KEY (`plan_id`) REFERENCES `plans`(`id`) ON UPDATE no action ON DELETE cascade,
	CONSTRAINT "tasks_status" CHECK(status IN ('todo', 'doing', 'done', 'blocked')),
	CONSTRAINT "tasks_effort" CHECK("effort" IS NULL OR effort IN ('S', 'M', 'L'))
);

USE [DevTest5]
GO

/****** Object:  Table [dbo].[dms_document]    Script Date: 13/10/2026 13:01:24 ******/
SET ANSI_NULLS ON
GO

SET QUOTED_IDENTIFIER ON
GO

CREATE TABLE [dbo].[dms_document](
	[id] [bigint] IDENTITY(1,1) NOT NULL,
	[dms_category_id] [int] NOT NULL,
	[name] [nvarchar](200) NOT NULL,
	[code] [nvarchar](100) NULL,
	[description] [nvarchar](max) NULL,
	[title] [nvarchar](500) NOT NULL,
	[icon] [nvarchar](1000) NULL,
	[current_version_id] [bigint] NULL,
	[sensitivity] [nvarchar](50) NULL,
	[is_archived] [bit] NOT NULL,
	[creation_date] [datetime] NULL,
	[created_by] [nvarchar](max) NULL,
	[date_updated] [datetime] NULL,
	[updated_by] [nvarchar](max) NULL,
	[status] [int] NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON, OPTIMIZE_FOR_SEQUENTIAL_KEY = OFF) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
GO

ALTER TABLE [dbo].[dms_document] ADD  DEFAULT ((0)) FOR [is_archived]
GO

ALTER TABLE [dbo].[dms_document]  WITH CHECK ADD  CONSTRAINT [fk_dms_document_current_version_id] FOREIGN KEY([current_version_id])
REFERENCES [dbo].[dms_document_version] ([id])
GO

ALTER TABLE [dbo].[dms_document] CHECK CONSTRAINT [fk_dms_document_current_version_id]
GO

ALTER TABLE [dbo].[dms_document]  WITH CHECK ADD  CONSTRAINT [fk_dms_document_dms_category_id] FOREIGN KEY([dms_category_id])
REFERENCES [dbo].[dms_category] ([id])
GO

ALTER TABLE [dbo].[dms_document] CHECK CONSTRAINT [fk_dms_document_dms_category_id]
GO

ALTER TABLE [dbo].[dms_document]  WITH CHECK ADD  CONSTRAINT [FK_dms_document_status_option_value] FOREIGN KEY([status])
REFERENCES [dbo].[option_value] ([id])
GO

ALTER TABLE [dbo].[dms_document] CHECK CONSTRAINT [FK_dms_document_status_option_value]
GO
USE [DevTest5]
GO

/****** Object:  Table [dbo].[dms_document_version]    Script Date: 13/10/2026 13:01:32 ******/
SET ANSI_NULLS ON
GO

SET QUOTED_IDENTIFIER ON
GO

CREATE TABLE [dbo].[dms_document_version](
	[id] [bigint] IDENTITY(1,1) NOT NULL,
	[dms_document_id] [bigint] NOT NULL,
	[version_number] [decimal](6, 2) NOT NULL,
	[status] [nvarchar](40) NOT NULL,
	[effective_from] [datetime] NULL,
	[effective_to] [datetime] NULL,
	[markdown] [nvarchar](max) NULL,
	[file] [varbinary](max) NULL,
	[blob_uri] [nvarchar](1000) NULL,
	[document_source_type] [nvarchar](50) NOT NULL,
	[hash] [varbinary](64) NULL,
	[is_archived] [bit] NOT NULL,
	[creation_date] [datetime] NULL,
	[created_by] [nvarchar](max) NULL,
	[date_updated] [datetime] NULL,
	[updated_by] [nvarchar](max) NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON, OPTIMIZE_FOR_SEQUENTIAL_KEY = OFF) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
GO

ALTER TABLE [dbo].[dms_document_version] ADD  DEFAULT ((0)) FOR [is_archived]
GO

ALTER TABLE [dbo].[dms_document_version]  WITH CHECK ADD  CONSTRAINT [fk_dms_document_version_dms_document_id] FOREIGN KEY([dms_document_id])
REFERENCES [dbo].[dms_document] ([id])
GO

ALTER TABLE [dbo].[dms_document_version] CHECK CONSTRAINT [fk_dms_document_version_dms_document_id]
GO

USE [DevTest5]
GO

/****** Object:  Table [dbo].[dms_document_comments]    Script Date: 13/10/2026 13:02:18 ******/
SET ANSI_NULLS ON
GO

SET QUOTED_IDENTIFIER ON
GO

CREATE TABLE [dbo].[dms_document_comments](
	[id] [bigint] IDENTITY(1,1) NOT NULL,
	[dms_document_id] [bigint] NOT NULL,
	[comment] [nvarchar](max) NULL,
	[creation_date] [datetime] NULL,
	[created_by] [nvarchar](max) NULL,
	[date_updated] [datetime] NULL,
	[updated_by] [nvarchar](max) NULL,
PRIMARY KEY CLUSTERED 
(
	[id] ASC
)WITH (PAD_INDEX = OFF, STATISTICS_NORECOMPUTE = OFF, IGNORE_DUP_KEY = OFF, ALLOW_ROW_LOCKS = ON, ALLOW_PAGE_LOCKS = ON, OPTIMIZE_FOR_SEQUENTIAL_KEY = OFF) ON [PRIMARY]
) ON [PRIMARY] TEXTIMAGE_ON [PRIMARY]
GO

ALTER TABLE [dbo].[dms_document_comments]  WITH CHECK ADD  CONSTRAINT [fk_dms_document_comments_dms_document_id] FOREIGN KEY([dms_document_id])
REFERENCES [dbo].[dms_document] ([id])
GO

ALTER TABLE [dbo].[dms_document_comments] CHECK CONSTRAINT [fk_dms_document_comments_dms_document_id]
GO



